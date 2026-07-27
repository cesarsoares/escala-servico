"""Impedimentos: dispensa, férias, curso, operação (regra 7.5).

Militar é pulado no período, mas mantém a vez (a fila não muda de ordem).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.referencia import TipoImpedimento


class Impedimento(Base):
    __tablename__ = "impedimento"
    __table_args__ = (
        CheckConstraint("fim >= inicio", name="ck_periodo"),
        Index("ix_impedimento_militar", "militar_id", "inicio", "fim"),  # "impedido no dia X?"
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    militar_id: Mapped[int] = mapped_column(
        ForeignKey("militar.id", ondelete="CASCADE"), nullable=False)
    tipo_impedimento_id: Mapped[int] = mapped_column(
        ForeignKey("tipo_impedimento.id"), nullable=False)
    inicio: Mapped[date] = mapped_column(Date, nullable=False)
    fim: Mapped[date] = mapped_column(Date, nullable=False)
    observacao: Mapped[str | None] = mapped_column(String(200))

    tipo: Mapped[TipoImpedimento] = relationship()
