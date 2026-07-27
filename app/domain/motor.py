"""Motor de rotação — quem é o próximo a servir (seção 6 das regras)."""
from __future__ import annotations

from datetime import date, datetime

from app.domain.antiguidade import comparar_antiguidade
from app.domain.folga import respeita_folga_minima
from app.domain.models import Cor, Escala, Impedimento, Participacao


def _data_minima() -> date:
    return date.min


def fila_ordenada(participacoes: list[Participacao], cor: Cor) -> list[Participacao]:
    """Ordena a fila: mais folgado primeiro; empate resolvido por antiguidade.

    Regra 6.2: 'mais folgado' = serviu há mais tempo naquela cor (data mais antiga).
    Quem nunca serviu (None) vai para o topo. Regra 6.3/seção 9: desempate.

    Quem não concorre nesta cor (regra 3.3.1) fica FORA da fila — não é o mesmo
    que ser pulado por impedimento (6.4): ali o militar guarda a vez, aqui ele
    nunca teve vez nesta cor.
    """
    ativos = [p for p in participacoes if p.ativo and p.serve_cor(cor)]  # regras 7.6 e 3.3.1

    def chave(p: Participacao):
        return p.ultimo_na_cor(cor) or _data_minima()

    def cmp(pa: Participacao, pb: Participacao) -> int:
        ka, kb = chave(pa), chave(pb)
        if ka != kb:
            return -1 if ka < kb else 1  # data mais antiga primeiro
        return comparar_antiguidade(pa.militar, pb.militar)

    from functools import cmp_to_key
    return sorted(ativos, key=cmp_to_key(cmp))


def disponivel(
    p: Participacao,
    dia: date,
    impedimentos: list[Impedimento],
    ultimo_termino_global: datetime | None,
    inicio_candidato: datetime,
    folga_minima_horas: int | None = None,
) -> bool:
    """Regra 7: sem impedimento no dia E respeitando a folga mínima da escala.

    `inicio_candidato` é o início do serviço a assumir (janela da escala de
    destino); `folga_minima_horas` é o piso dessa escala (regra 7.4.2);
    `ultimo_termino_global` é o término do último serviço do militar em qualquer
    cor e qualquer escala concorrente (regra 7.4).
    """
    for imp in impedimentos:
        if imp.militar_id == p.militar.id and imp.cobre(dia):
            return False  # regra 7.5
    return respeita_folga_minima(ultimo_termino_global, inicio_candidato, folga_minima_horas)


def proximos(
    escala: Escala,
    participacoes: list[Participacao],
    cor: Cor,
    dia: date,
    impedimentos: list[Impedimento],
    ultimo_termino_por_militar: dict[int, datetime],
) -> list[Participacao]:
    """Escolhe os mais folgados disponíveis para preencher os postos do dia.

    Regra 6.1: percorre a fila do mais folgado ao menos folgado e pega os
    `escala.postos` primeiros disponíveis. A janela e a folga usadas são as da
    própria escala (regras 2.4 e 7.4.2). Quem está impedido é apenas pulado; a
    fila não muda de ordem (regra 6.4).

    Se retornar MENOS que `escala.postos`, o efetivo é insuficiente para o piso
    naquele dia (regra 7.8): cabe ao caller avisar o gestor.

    `ultimo_termino_por_militar` traz o término do serviço mais recente de cada
    militar nas escalas concorrentes (montado pela camada de serviço).
    """
    inicio_candidato = escala.inicio_em(dia)
    escolhidos: list[Participacao] = []
    for p in fila_ordenada(participacoes, cor):
        if len(escolhidos) >= escala.postos:
            break
        ultimo = ultimo_termino_por_militar.get(p.militar.id)
        if disponivel(p, dia, impedimentos, ultimo, inicio_candidato, escala.folga_minima_horas):
            escolhidos.append(p)
    return escolhidos


def proximo(
    escala: Escala,
    participacoes: list[Participacao],
    cor: Cor,
    dia: date,
    impedimentos: list[Impedimento],
    ultimo_termino_por_militar: dict[int, datetime],
) -> Participacao | None:
    """Caso de 1 posto: o primeiro da fila que estiver disponível (regra 6.2).

    Açúcar para `proximos(...)` pegando o primeiro escolhido.
    """
    escolhidos = proximos(
        escala, participacoes, cor, dia, impedimentos, ultimo_termino_por_militar,
    )
    return escolhidos[0] if escolhidos else None
