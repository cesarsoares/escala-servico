"""Serviço (militar-dia-posto) e permuta.

O serviço é a unidade de escalação efetiva. A folga fica colada em `militar_id`
(o ESCALADO), nunca no substituto (regra 9). `dia`, `cor`, `inicio_dt` e
`termino_dt` são gravados como retrato do momento da escalação:
  - a fila (mais folgado) deriva daqui — max(dia)/max(termino_dt) por militar/cor;
  - `termino_dt` alimenta a folga mínima entre escalas concorrentes (regra 7).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.domain.models import Cor
from app.models.enums import cor_enum

if TYPE_CHECKING:
    from app.models.militar import Militar


class Servico(Base):
    __tablename__ = "servico"
    __table_args__ = (
        UniqueConstraint("posto_id", "dia", name="uq_servico_posto_dia"),  # 1 militar por vaga/dia
        Index("ix_servico_militar_cor", "militar_id", "cor", "dia"),   # fila "mais folgado" por cor
        Index("ix_servico_escala_dia", "escala_id", "dia"),            # escala/previsão por período
        Index("ix_servico_termino", "militar_id", "termino_dt"),       # folga mínima entre concorrentes
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    escala_id: Mapped[int] = mapped_column(ForeignKey("escala.id"), nullable=False)
    posto_id: Mapped[int] = mapped_column(ForeignKey("posto.id"), nullable=False)
    militar_id: Mapped[int] = mapped_column(ForeignKey("militar.id"), nullable=False)  # o escalado
    dia: Mapped[date] = mapped_column(Date, nullable=False)             # dia em que o serviço começa
    cor: Mapped[Cor] = mapped_column(cor_enum, nullable=False)
    inicio_dt: Mapped[datetime] = mapped_column(DateTime, nullable=False)   # escala.inicio_em(dia)
    termino_dt: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # escala.termino_em(dia)

    # read-only: a escalação grava por militar_id; a relação só serve à consulta
    militar: Mapped[Militar] = relationship("Militar", viewonly=True, lazy="raise")
    permuta: Mapped[Permuta | None] = relationship(
        back_populates="servico", uselist=False, cascade="all, delete-orphan")


class Permuta(Base):
    """Troca — registro puro, ancorado no serviço (regra 9).

    Sem retribuição automática nem recálculo de folga. A folga NÃO muda de dono:
    segue em servico.militar_id. Negar (no código) se ferir a folga mínima de
    quem vai cobrir.
    """
    __tablename__ = "permuta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    servico_id: Mapped[int] = mapped_column(
        ForeignKey("servico.id", ondelete="CASCADE"), nullable=False, unique=True)
    militar_substituto_id: Mapped[int] = mapped_column(ForeignKey("militar.id"), nullable=False)
    autorizado_por: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"))
    criado_em: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now())
    observacao: Mapped[str | None] = mapped_column(String(200))

    servico: Mapped[Servico] = relationship(back_populates="permuta")
