"""Tabelas de referência (dados fixos por lei ou gerenciados pelo gestor).

organizacao_militar, circulo_hierarquico, posto_graduacao, tipo_impedimento.
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, false, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class OrganizacaoMilitar(Base):
    """OM de origem do militar (regra 3.2).

    `propria` marca a OM DONA da instalação — a que aparece no cabeçalho e no
    rodapé das telas. As demais são as OMs de origem de quem serve aqui (num QG,
    o efetivo vem de várias). Uma instalação por OM (regra 13.2), então só uma
    linha pode ter `propria=True`; quem garante é `services/configuracao.py`,
    que desmarca as outras na mesma transação.
    """
    __tablename__ = "organizacao_militar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    sigla: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    propria: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false())


class CirculoHierarquico(Base):
    """Círculo hierárquico (Lei 6.880/80, art. 16). Fixo por lei.

    `eh_praca` responde a regra 9.5 (desempate por nº de antiguidade das praças).
    """
    __tablename__ = "circulo_hierarquico"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)  # maior = mais antigo
    eh_praca: Mapped[bool] = mapped_column(Boolean, nullable=False)

    postos_graduacao: Mapped[list[PostoGraduacao]] = relationship(back_populates="circulo")


class PostoGraduacao(Base):
    """Posto (oficial) ou graduação (praça) — patente. Ordem do art. 16.

    Editável pelo gestor em Configurações: a tabela nasce semeada com a escala
    da Lei 6.880/80, mas a OM pode não ter uma graduação, usar nomenclatura
    própria ou precisar acrescentar. Por isso:

    - `ordem_hierarquica` NÃO é mais `unique`. Só a ordem RELATIVA importa (é o
      que o desempate 9.1 usa), e a unicidade impedia mover duas linhas de lugar
      numa transação só — o SQLite não adia a checagem. Quem impede repetição é
      `services/configuracao.py`, que renumera a coluna a cada mudança.
    - `ativo=False` esconde a graduação dos formulários sem apagá-la: apagar
      quebraria a FK dos militares já cadastrados e o histórico deles.
    """
    __tablename__ = "posto_graduacao"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sigla: Mapped[str] = mapped_column(String(12), nullable=False, unique=True)  # 'Cap', '2º Sgt'
    nome: Mapped[str] = mapped_column(String(60), nullable=False)
    ordem_hierarquica: Mapped[int] = mapped_column(Integer, nullable=False)  # art. 16
    circulo_id: Mapped[int] = mapped_column(ForeignKey("circulo_hierarquico.id"), nullable=False)
    ativo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true())

    circulo: Mapped[CirculoHierarquico] = relationship(back_populates="postos_graduacao")


class TipoImpedimento(Base):
    """Dispensa, férias, curso, operação... (regra 7.5).

    Varia muito de OM para OM — por isso é editável em Configurações. Mesma
    razão do posto/graduação para desativar em vez de apagar: há impedimentos
    lançados apontando para o tipo.
    """
    __tablename__ = "tipo_impedimento"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    ativo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true())


class Configuracao(Base):
    """Ajustes da instalação, em chave/valor (regra 13.2 — uma OM por instalação).

    Chave/valor, e não colunas, porque o conjunto cresce com o uso e cada
    ajuste novo não merece uma migração. O que TEM integridade referencial
    (qual é a OM da casa) não mora aqui: é a coluna `organizacao_militar.propria`.

    Chaves em uso: ver `services/configuracao.CHAVES`.
    """
    __tablename__ = "configuracao"

    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(String(500), nullable=False, default="")
