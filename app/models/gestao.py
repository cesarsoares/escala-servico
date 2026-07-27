"""Gestão e auditoria (regra 11). Consulta é aberta; gestão exige login."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Usuario(Base):
    """Gestor. Múltiplos gestores por instalação (regra 11)."""
    __tablename__ = "usuario"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    senha_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())


class Auditoria(Base):
    """Histórico de todas as alterações manuais (regra 11)."""
    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"))
    entidade: Mapped[str] = mapped_column(String(60), nullable=False)   # ex.: 'servico'
    entidade_id: Mapped[int | None] = mapped_column(Integer)
    acao: Mapped[str] = mapped_column(String(20), nullable=False)       # criar|alterar|excluir
    dados_antes: Mapped[dict | None] = mapped_column(JSON)
    dados_depois: Mapped[dict | None] = mapped_column(JSON)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now())
