"""Folga mínima — configurável por escala, com piso rígido (seção 7 das regras).

A folga é sempre medida entre o **término** do serviço anterior e o **início**
do próximo. Como a janela (horário de início + duração) é configurável por
escala (regra 2.4), quem chama passa os instantes já calculados — tipicamente via
`Escala.termino_em(dia)` e `Escala.inicio_em(dia)`.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# Regra 7.2.2 — a folga mínima NUNCA pode ser inferior a 24h, para nenhuma
# escala ("saiu no dia X, não assume no dia X"). É um piso inquebrável.
PISO_FOLGA_HORAS = 24

# Regra 7.2.1 — default sugerido (prática das escalas de oficial do QG). Não é
# uma trava: cada escala configura o seu valor, respeitado o piso acima.
FOLGA_DEFAULT_HORAS = 48


def folga_efetiva_horas(folga_minima_horas: int | None) -> int:
    """Resolve a folga aplicável: o valor configurado, nunca abaixo do piso.

    Regra 7.2.1/7.2.2. `None` cai no default sugerido (48h).
    """
    horas = FOLGA_DEFAULT_HORAS if folga_minima_horas is None else folga_minima_horas
    return max(horas, PISO_FOLGA_HORAS)


def respeita_folga_minima(
    ultimo_termino: datetime | None,
    inicio_candidato: datetime,
    folga_minima_horas: int | None = None,
) -> bool:
    """True se o militar já cumpriu a folga mínima para o próximo serviço.

    Regra 7.2/7.3: entre o `ultimo_termino` (fim do serviço mais recente do
    militar, em qualquer cor e qualquer escala concorrente — regra 7.4) e o
    `inicio_candidato` (início do serviço a assumir) precisa haver ao menos a
    folga mínima DA ESCALA DE DESTINO (regra 7.4.2). `folga_minima_horas` é o
    valor configurado nessa escala; `None` usa o default de 48h. O piso rígido
    de 24h é aplicado sempre (regra 7.2.2).

    Ambos os instantes já vêm com a janela real da respectiva escala embutida
    (regra 2.4), então serviços de janelas diferentes se comparam corretamente.
    """
    if ultimo_termino is None:
        return True
    folga = timedelta(hours=folga_efetiva_horas(folga_minima_horas))
    return inicio_candidato - ultimo_termino >= folga
