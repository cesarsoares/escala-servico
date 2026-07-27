"""configuração da OM: OM própria, tabelas de referência editáveis

O sistema deixou de ser "o QG do CMS" e passa a ser instalado por qualquer OM,
inclusive batalhão (regra 13.2). O que era fixo no código ou no .env vira dado:

  - organizacao_militar.propria  -> qual é a OM DONA da instalação (cabeçalho
    e rodapé das telas). Antes vinha de om_sigla/om_nome no .env.
  - posto_graduacao.ativo        -> esconder uma graduação que a OM não usa,
    sem apagar (a FK dos militares e o histórico dependem dela).
  - posto_graduacao.ordem_hierarquica DEIXA de ser UNIQUE: a ordem passa a ser
    editável e mover duas linhas numa transação só é impossível com a checagem
    imediata do SQLite. Quem impede repetição é services/configuracao.py, que
    renumera a coluna a cada mudança.
  - tipo_impedimento.ativo       -> mesma razão do posto/graduação.
  - configuracao (nova)          -> chave/valor dos ajustes da instalação.

Revision ID: c4e81af5d072
Revises: b7f3c2a19d84
Create Date: 2026-07-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4e81af5d072'
down_revision: Union[str, None] = 'b7f3c2a19d84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# A UNIQUE de ordem_hierarquica nasceu SEM NOME na migração inicial, e o SQLite
# não deixa remover constraint anônima por reflexão. `copy_from` descreve a
# tabela como ela está hoje, dando nome à constraint para poder derrubá-la; o
# batch então recria a tabela a partir desta definição.
_POSTO_ANTIGO = sa.Table(
    "posto_graduacao", sa.MetaData(),
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column("sigla", sa.String(length=12), nullable=False),
    sa.Column("nome", sa.String(length=60), nullable=False),
    sa.Column("ordem_hierarquica", sa.Integer(), nullable=False),
    sa.Column("circulo_id", sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(["circulo_id"], ["circulo_hierarquico.id"]),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint("sigla", name="uq_posto_graduacao_sigla"),
    sa.UniqueConstraint("ordem_hierarquica", name="uq_posto_graduacao_ordem"),
)


def upgrade() -> None:
    op.create_table(
        "configuracao",
        sa.Column("chave", sa.String(length=60), primary_key=True),
        sa.Column("valor", sa.String(length=500), nullable=False),
    )

    with op.batch_alter_table("organizacao_militar") as batch:
        batch.add_column(sa.Column("propria", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))

    # batch_alter_table recria a tabela no SQLite: é assim que a UNIQUE de
    # ordem_hierarquica sai. Em PostgreSQL vira um ALTER normal.
    with op.batch_alter_table("posto_graduacao", copy_from=_POSTO_ANTIGO) as batch:
        batch.add_column(sa.Column("ativo", sa.Boolean(), nullable=False,
                                   server_default=sa.true()))
        batch.drop_constraint("uq_posto_graduacao_ordem", type_="unique")

    with op.batch_alter_table("tipo_impedimento") as batch:
        batch.add_column(sa.Column("ativo", sa.Boolean(), nullable=False,
                                   server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table("tipo_impedimento") as batch:
        batch.drop_column("ativo")

    with op.batch_alter_table("posto_graduacao") as batch:
        batch.create_unique_constraint("uq_posto_graduacao_ordem", ["ordem_hierarquica"])
        batch.drop_column("ativo")

    with op.batch_alter_table("organizacao_militar") as batch:
        batch.drop_column("propria")

    op.drop_table("configuracao")
