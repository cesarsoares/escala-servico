"""Tabelas do banco (SQLAlchemy 2.0). Derivadas de db/schema.sql (regras v2).

Importar tudo aqui garante que `Base.metadata` conheça todas as tabelas
(necessário para o Alembic autogenerate e para create_all nos testes).
"""
from app.models.calendario import Feriado, OverrideDia
from app.models.escala import Escala, EscalaConcorrente, Participacao, Posto
from app.models.gestao import Auditoria, Usuario
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import (
    CirculoHierarquico,
    Configuracao,
    OrganizacaoMilitar,
    PostoGraduacao,
    TipoImpedimento,
)
from app.models.servico import Permuta, Servico

__all__ = [
    "OrganizacaoMilitar",
    "CirculoHierarquico",
    "PostoGraduacao",
    "TipoImpedimento",
    "Configuracao",
    "Militar",
    "Escala",
    "Posto",
    "EscalaConcorrente",
    "Participacao",
    "Feriado",
    "OverrideDia",
    "Impedimento",
    "Servico",
    "Permuta",
    "Usuario",
    "Auditoria",
]
