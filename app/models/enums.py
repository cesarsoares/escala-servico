"""Tipos SQL compartilhados entre modelos.

O enum `cor` é usado por `servico` e `override_dia`. Compartilhar a MESMA
instância `Enum` evita que o SQLAlchemy tente criar o tipo duas vezes no
PostgreSQL (regras 2.5/2.6).
"""
from sqlalchemy import Enum

from app.domain.models import Cor

# values_callable: grava o VALOR ('preta'/'vermelha'), não o nome do Enum
# ('PRETA'/'VERMELHA'), mantendo coerência com o domínio e com db/schema.sql.
cor_enum = Enum(Cor, name="cor", values_callable=lambda enum: [m.value for m in enum])
