"""Escala e seus postos (seção 4). Entidade criada/excluída pelo gestor."""
from __future__ import annotations

from datetime import time

from sqlalchemy import (
    Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Time,
    UniqueConstraint, text, true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Escala(Base):
    __tablename__ = "escala"
    __table_args__ = (
        # piso rígido de 24h (regra 7.2); default 48h aplicado no código quando NULL
        CheckConstraint("folga_minima_horas IS NULL OR folga_minima_horas >= 24",
                        name="ck_folga_piso"),
        CheckConstraint("duracao_horas > 0", name="ck_duracao"),
        # regra 4.5: uma escala precisa rodar ao menos uma cor (senão nunca escala)
        CheckConstraint("tem_preta OR tem_vermelha", name="ck_ao_menos_uma_cor"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    tem_preta: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    tem_vermelha: Mapped[bool] = mapped_column(  # Museu (regra 4.5)
        Boolean, nullable=False, default=True, server_default=true())
    folga_minima_horas: Mapped[int | None] = mapped_column(Integer)   # regra 7.2 (NULL = default 48h)
    inicio_servico: Mapped[time] = mapped_column(  # regra 2.4
        Time, nullable=False, default=time(8, 0), server_default=text("'08:00:00'"))
    duracao_horas: Mapped[int] = mapped_column(  # regra 2.4
        Integer, nullable=False, default=24, server_default=text("24"))
    ativa: Mapped[bool] = mapped_column(  # regra 4.4
        Boolean, nullable=False, default=True, server_default=true())

    postos: Mapped[list[Posto]] = relationship(
        back_populates="escala", cascade="all, delete-orphan")
    participacoes: Mapped[list[Participacao]] = relationship(
        back_populates="escala", cascade="all, delete-orphan")


class Posto(Base):
    """Cada vaga da escala num dia (regra 2.5). Nº de postos = nº de linhas."""
    __tablename__ = "posto"
    __table_args__ = (UniqueConstraint("escala_id", "ordem", name="uq_posto_escala_ordem"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    escala_id: Mapped[int] = mapped_column(
        ForeignKey("escala.id", ondelete="CASCADE"), nullable=False)
    ordem: Mapped[int] = mapped_column(Integer, nullable=False)   # 1..N dentro da escala
    rotulo: Mapped[str | None] = mapped_column(String(80))        # ex.: 'Comandante da Guarda'

    escala: Mapped[Escala] = relationship(back_populates="postos")


class EscalaConcorrente(Base):
    """Concorrência entre escalas: relação explícita e SIMÉTRICA (regra 7.4.1).

    Uma linha por par, com menor < maior, para não duplicar a simetria.
    """
    __tablename__ = "escala_concorrente"
    __table_args__ = (
        CheckConstraint("escala_menor_id < escala_maior_id", name="ck_par_ordenado"),
    )

    escala_menor_id: Mapped[int] = mapped_column(
        ForeignKey("escala.id", ondelete="CASCADE"), primary_key=True)
    escala_maior_id: Mapped[int] = mapped_column(
        ForeignKey("escala.id", ondelete="CASCADE"), primary_key=True)


class Participacao(Base):
    """Vínculo militar <-> escala (regra 3.3).

    `ativo` = participa hoje; isenção permanente = não-participação (regra 7.6).
    SEM colunas de última data: a fila deriva de `servico`.
    """
    __tablename__ = "participacao"
    __table_args__ = (
        UniqueConstraint("militar_id", "escala_id", name="uq_participacao_militar_escala"),
        Index("ix_participacao_escala", "escala_id",  # participantes ativos da escala (parcial)
              postgresql_where=text("ativo"), sqlite_where=text("ativo")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    militar_id: Mapped[int] = mapped_column(
        ForeignKey("militar.id", ondelete="CASCADE"), nullable=False)
    escala_id: Mapped[int] = mapped_column(
        ForeignKey("escala.id", ondelete="CASCADE"), nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())

    escala: Mapped[Escala] = relationship(back_populates="participacoes")
