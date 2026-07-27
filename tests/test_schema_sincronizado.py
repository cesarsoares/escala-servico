"""Guarda contra divergência entre models, migrações Alembic e db/schema.sql.

Alterar uma coluna em `app/models/` e esquecer a migração produz um banco de
produção diferente do que o código espera — e o erro só aparece no deploy. O
primeiro teste sobe um banco vazio APENAS com as migrações e confere que o
autogenerate não teria mais nada a fazer (o mesmo que `alembic check`).

O segundo confere `db/schema.sql`, que é documento de leitura mantido à mão e
já ficou desatualizado uma vez sem ninguém notar. Compara só tabelas e nomes de
colunas: tipos e constraints continuam por conta de quem lê.
"""
import os
import re

import pytest
from alembic import command
from alembic.autogenerate import produce_migrations
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app.database import Base
import app.models  # noqa: F401 — registra as tabelas em Base.metadata

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def banco_migrado(tmp_path, monkeypatch):
    """Banco criado só pelas migrações (nada de create_all)."""
    url = f"sqlite+pysqlite:///{tmp_path / 'migrado.sqlite3'}"
    # env.py lê a URL de app.config.settings, não do alembic.ini.
    monkeypatch.setenv("DATABASE_URL", url)
    from app import config as app_config
    monkeypatch.setattr(app_config.settings, "database_url", url)

    cfg = Config(os.path.join(RAIZ, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(RAIZ, "alembic"))
    command.upgrade(cfg, "head")
    return url


def test_migracoes_cobrem_todos_os_models(banco_migrado):
    engine = create_engine(banco_migrado)
    with engine.connect() as conn:
        contexto = MigrationContext.configure(
            conn, opts={"target_metadata": Base.metadata, "compare_type": True},
        )
        diff = produce_migrations(contexto, Base.metadata)

    pendentes = diff.upgrade_ops.as_diffs()
    assert not pendentes, (
        "models e migrações divergiram — gere a migração:\n  "
        + "\n  ".join(str(p) for p in pendentes)
    )


# --- db/schema.sql (documento de referência) ---------------------------------
PALAVRAS_NAO_COLUNA = {"primary", "unique", "constraint", "foreign", "check", "--"}


def _tabelas_do_schema_sql() -> dict[str, set[str]]:
    """Tabelas e colunas declaradas no schema.sql (leitura tosca, sem parser SQL)."""
    with open(os.path.join(RAIZ, "db", "schema.sql"), encoding="utf-8") as f:
        sql = f.read()

    tabelas: dict[str, set[str]] = {}
    for nome, corpo in re.findall(r"CREATE TABLE (\w+)\s*\((.*?)\n\);", sql, re.S):
        colunas = set()
        for linha in corpo.splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("--"):
                continue
            primeira = linha.split()[0].lower()
            if primeira in PALAVRAS_NAO_COLUNA:
                continue
            colunas.add(primeira)
        tabelas[nome] = colunas
    return tabelas


def test_schema_sql_cobre_as_mesmas_tabelas_e_colunas():
    documentado = _tabelas_do_schema_sql()
    real = {nome: set(t.columns.keys()) for nome, t in Base.metadata.tables.items()}

    assert set(documentado) == set(real), (
        "db/schema.sql e os models divergem nas TABELAS — "
        f"só no documento: {set(documentado) - set(real)}; "
        f"só nos models: {set(real) - set(documentado)}"
    )
    divergentes = {
        nome: {"só no documento": documentado[nome] - real[nome],
               "só nos models": real[nome] - documentado[nome]}
        for nome in real if documentado[nome] != real[nome]
    }
    assert not divergentes, f"db/schema.sql desatualizado: {divergentes}"
