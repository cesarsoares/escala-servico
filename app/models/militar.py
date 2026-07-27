"""Militar (regras 3.2, 9.x; Lei 6.880/80, art. 16-17)."""
from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, text, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao


class Militar(Base):
    __tablename__ = "militar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # SERIAL
    nome_guerra: Mapped[str] = mapped_column(String(60), nullable=False)
    nome_completo: Mapped[str] = mapped_column(String(160), nullable=False)
    # Identidade/antiguidade OPCIONAIS: o efetivo pode entrar pela planilha (só
    # nome+posto+OM) e ser completado depois pela ficha. varchar UNIQUE preserva
    # zero à esquerda; nullable+unique permite vários NULL (SQLite/PG).
    identidade: Mapped[str | None] = mapped_column(String(20), unique=True)
    cpf: Mapped[str | None] = mapped_column(String(14), unique=True)

    posto_graduacao_id: Mapped[int] = mapped_column(ForeignKey("posto_graduacao.id"), nullable=False)
    om_id: Mapped[int] = mapped_column(ForeignKey("organizacao_militar.id"), nullable=False)

    data_promocao: Mapped[date | None] = mapped_column(Date)                   # regra 9.2
    data_praca: Mapped[date | None] = mapped_column(Date)                      # regra 9.3
    data_nascimento: Mapped[date | None] = mapped_column(Date)                 # art. 17
    numero_antiguidade: Mapped[int | None] = mapped_column(Integer)            # regra 9.5 (só praças)
    ordem_manual: Mapped[int] = mapped_column(  # regra 9.4
        Integer, nullable=False, default=0, server_default=text("0"))
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())

    posto_graduacao: Mapped[PostoGraduacao] = relationship()
    om: Mapped[OrganizacaoMilitar] = relationship()
