"""Leitura do calendário para o motor (seção 5).

Traduz as tabelas `feriado` e `override_dia` nos conjuntos de datas que
`domain.calendario.classificar_dia` espera. Carregar uma vez e reusar ao
escalar um período inteiro.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Cor
from app.models.calendario import Feriado, OverrideDia


def feriados(session: Session, inicio: date | None = None, fim: date | None = None) -> set[date]:
    """Datas de feriado (nacionais embutidos + adicionados pelo gestor, regra 5.2)."""
    stmt = select(Feriado.data)
    if inicio is not None:
        stmt = stmt.where(Feriado.data >= inicio)
    if fim is not None:
        stmt = stmt.where(Feriado.data <= fim)
    return set(session.scalars(stmt))


def _overrides(session: Session, cor: Cor, inicio: date | None, fim: date | None) -> set[date]:
    stmt = select(OverrideDia.data).where(OverrideDia.cor == cor)
    if inicio is not None:
        stmt = stmt.where(OverrideDia.data >= inicio)
    if fim is not None:
        stmt = stmt.where(OverrideDia.data <= fim)
    return set(session.scalars(stmt))


def overrides_vermelha(session: Session, inicio: date | None = None, fim: date | None = None) -> set[date]:
    """Dias que o gestor forçou como vermelha (regra 5.3)."""
    return _overrides(session, Cor.VERMELHA, inicio, fim)


def overrides_preta(session: Session, inicio: date | None = None, fim: date | None = None) -> set[date]:
    """Dias que o gestor forçou como preta (regra 5.3; ex.: feriado trabalhado)."""
    return _overrides(session, Cor.PRETA, inicio, fim)
