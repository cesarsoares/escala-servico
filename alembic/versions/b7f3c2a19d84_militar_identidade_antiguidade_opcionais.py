"""militar: identidade/cpf/datas de antiguidade opcionais

Permite carregar o efetivo a partir da planilha (só nome+posto+OM) e completar
identidade/CPF/datas de promoção e praça depois, pela ficha. As colunas
identidade e cpf seguem UNIQUE (nullable+unique aceita vários NULL).

Revision ID: b7f3c2a19d84
Revises: 05a044b93121
Create Date: 2026-07-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7f3c2a19d84'
down_revision: Union[str, None] = '05a044b93121'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# colunas que passam a aceitar NULL (e voltam a NOT NULL no downgrade)
_COLS = (
    ("identidade", sa.String(length=20)),
    ("cpf", sa.String(length=14)),
    ("data_promocao", sa.Date()),
    ("data_praca", sa.Date()),
)


def upgrade() -> None:
    with op.batch_alter_table("militar") as batch:
        for nome, tipo in _COLS:
            batch.alter_column(nome, existing_type=tipo, nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("militar") as batch:
        for nome, tipo in _COLS:
            batch.alter_column(nome, existing_type=tipo, nullable=False)
