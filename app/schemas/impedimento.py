"""Schemas de impedimento: dispensa, férias, curso, operação (regra 7.5).

Militar é pulado no período, mas mantém a vez (a fila não muda de ordem).
"""
from __future__ import annotations

from datetime import date

from pydantic import Field, model_validator

from app.schemas.base import Entrada, Resposta
from app.schemas.referencia import TipoImpedimentoOut


class ImpedimentoCreate(Entrada):
    militar_id: int
    tipo_impedimento_id: int
    inicio: date
    fim: date
    observacao: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _periodo_valido(self):
        """Espelha a CheckConstraint ck_periodo (fim >= inicio)."""
        if self.fim < self.inicio:
            raise ValueError("'fim' não pode ser anterior a 'inicio'")
        return self


class ImpedimentoUpdate(Entrada):
    tipo_impedimento_id: int | None = None
    inicio: date | None = None
    fim: date | None = None
    observacao: str | None = Field(default=None, max_length=200)


class ImpedimentoOut(Resposta):
    id: int
    militar_id: int
    tipo_impedimento_id: int
    inicio: date
    fim: date
    observacao: str | None


class ImpedimentoDetalheOut(ImpedimentoOut):
    """Impedimento com o tipo aninhado (para exibição)."""
    tipo: TipoImpedimentoOut
