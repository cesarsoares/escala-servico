"""participação restrita a uma cor (regra 3.3.1)

Em cada escala, o participante concorre nas duas cores (padrão) ou em apenas
uma delas — é o caso do militar cuja função o impede de servir em dia útil:
participa da escala, mas só é escalado em fim de semana e feriado.

Não se confunde com `escala.tem_preta/tem_vermelha` (regra 4.2), que diz em que
cores a ESCALA roda. Aqui a escala roda as duas e a PESSOA concorre em uma só.

As colunas nascem TRUE: todo vínculo já existente continua concorrendo nas duas
cores, que é como o sistema se comportava antes desta regra.

Revision ID: e2b6d1a7c9f3
Revises: c4e81af5d072
Create Date: 2026-07-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e2b6d1a7c9f3'
down_revision: Union[str, None] = 'c4e81af5d072'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Acrescentar um CHECK obriga o SQLite a recriar a tabela (batch). `copy_from`
# descreve a participação como ela está hoje para que a recriação não dependa de
# reflexão — em especial do índice PARCIAL `ix_participacao_escala`, cujo
# `WHERE ativo` a reflexão do SQLite não devolve e a cópia perderia em silêncio.
_ANTES = sa.Table(
    "participacao", sa.MetaData(),
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column("militar_id", sa.Integer(), nullable=False),
    sa.Column("escala_id", sa.Integer(), nullable=False),
    sa.Column("ativo", sa.Boolean(), server_default=sa.text("1"), nullable=False),
    sa.ForeignKeyConstraint(["escala_id"], ["escala.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["militar_id"], ["militar.id"], ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint("militar_id", "escala_id", name="uq_participacao_militar_escala"),
    sa.Index("ix_participacao_escala", "escala_id",
             postgresql_where=sa.text("ativo"), sqlite_where=sa.text("ativo")),
)

_DEPOIS = sa.Table(
    "participacao", sa.MetaData(),
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column("militar_id", sa.Integer(), nullable=False),
    sa.Column("escala_id", sa.Integer(), nullable=False),
    sa.Column("ativo", sa.Boolean(), server_default=sa.text("1"), nullable=False),
    sa.Column("serve_preta", sa.Boolean(), server_default=sa.text("1"), nullable=False),
    sa.Column("serve_vermelha", sa.Boolean(), server_default=sa.text("1"), nullable=False),
    sa.CheckConstraint("serve_preta OR serve_vermelha", name="ck_participacao_cor"),
    sa.ForeignKeyConstraint(["escala_id"], ["escala.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["militar_id"], ["militar.id"], ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint("militar_id", "escala_id", name="uq_participacao_militar_escala"),
    sa.Index("ix_participacao_escala", "escala_id",
             postgresql_where=sa.text("ativo"), sqlite_where=sa.text("ativo")),
)


def upgrade() -> None:
    with op.batch_alter_table("participacao", copy_from=_ANTES, recreate="always") as batch:
        batch.add_column(sa.Column("serve_preta", sa.Boolean(), nullable=False,
                                   server_default=sa.true()))
        batch.add_column(sa.Column("serve_vermelha", sa.Boolean(), nullable=False,
                                   server_default=sa.true()))
        # participar sem concorrer em cor nenhuma seria não participar
        batch.create_check_constraint("ck_participacao_cor",
                                      sa.text("serve_preta OR serve_vermelha"))


def downgrade() -> None:
    with op.batch_alter_table("participacao", copy_from=_DEPOIS, recreate="always") as batch:
        batch.drop_constraint("ck_participacao_cor", type_="check")
        batch.drop_column("serve_vermelha")
        batch.drop_column("serve_preta")
