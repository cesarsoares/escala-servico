"""Calendário: feriados e overrides de cor (seção 5)."""
from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, Integer, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.domain.models import Cor
from app.models.enums import cor_enum


class Feriado(Base):
    """Feriados nacionais embutidos (nacional=True) + adicionados pelo gestor (regra 5.2)."""
    __tablename__ = "feriado"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    nacional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())


class OverrideDia(Base):
    """Gestor declara um dia específico como vermelha, com observação (regra 5.3)."""
    __tablename__ = "override_dia"

    data: Mapped[date] = mapped_column(Date, primary_key=True)
    cor: Mapped[Cor] = mapped_column(
        cor_enum, nullable=False, default=Cor.VERMELHA, server_default=Cor.VERMELHA.value)
    observacao: Mapped[str | None] = mapped_column(String(200))
