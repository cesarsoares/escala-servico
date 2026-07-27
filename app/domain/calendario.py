"""Classificação do dia em preta ou vermelha (seção 5 das regras)."""
from __future__ import annotations

from datetime import date

from app.domain.models import Cor


def classificar_dia(
    dia: date,
    feriados: set[date],
    override_vermelha: set[date] | None = None,
    override_preta: set[date] | None = None,
) -> Cor:
    """Retorna a cor do dia.

    - Override do comando VENCE tudo (regra 5.3): o gestor pode forçar um dia
      como vermelha (ex.: véspera de feriado) OU como preta (ex.: feriado
      trabalhado normalmente). Um dia não pode estar nos dois (é PK por data).
    - Sem override: sábado, domingo ou feriado -> vermelha (regras 5.1, 5.2);
      caso contrário -> preta.
    """
    override_vermelha = override_vermelha or set()
    override_preta = override_preta or set()
    if dia in override_preta:
        return Cor.PRETA
    if dia in override_vermelha:
        return Cor.VERMELHA
    if dia in feriados:
        return Cor.VERMELHA
    if dia.weekday() >= 5:  # 5 = sábado, 6 = domingo
        return Cor.VERMELHA
    return Cor.PRETA
