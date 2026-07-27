"""Schemas do calendário: feriados e overrides de cor (seção 5)."""
from __future__ import annotations

from datetime import date

from pydantic import Field

from app.domain.models import Cor
from app.schemas.base import Entrada, Resposta


# --- Feriado (regra 5.2) ---
class FeriadoCreate(Entrada):
    data: date
    nome: str = Field(max_length=120)
    nacional: bool = False   # embutidos = True; gestor adiciona com False


class FeriadoUpdate(Entrada):
    nome: str | None = Field(default=None, max_length=120)
    nacional: bool | None = None


class FeriadoOut(Resposta):
    id: int
    data: date
    nome: str
    nacional: bool


# --- Override de dia: gestor força a cor de um dia (regra 5.3) ---
class OverrideDiaUpsert(Entrada):
    data: date
    cor: Cor = Cor.VERMELHA
    observacao: str | None = Field(default=None, max_length=200)


class OverrideDiaOut(Resposta):
    data: date
    cor: Cor
    observacao: str | None
