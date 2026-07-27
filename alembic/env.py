"""Ambiente Alembic — usa Base.metadata dos modelos e a URL do app.config."""
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context

from app.config import settings
from app.database import Base
import app.models  # noqa: F401  — registra todas as tabelas em Base.metadata

config = context.config
# URL vem do app (settings), não do alembic.ini, para uma única fonte de verdade.
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=settings.database_url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    eh_sqlite = settings.database_url.startswith("sqlite")
    with connectable.connect() as connection:
        # No SQLite, alterar coluna é RECRIAR a tabela (batch mode): alembic
        # copia os dados, dropa a original e renomeia a nova. Com as FKs ligadas
        # — e `app/database.py` as liga em toda conexão —, o DROP da tabela-pai
        # falha assim que existe uma linha filha: acrescentar uma coluna em
        # `organizacao_militar` quebrava por causa de `militar.om_id`.
        # Desligar durante a migração é o procedimento recomendado; ao voltar,
        # a checagem confirma que a cópia não deixou referência órfã.
        #
        # O PRAGMA vai no cursor CRU de propósito: dentro de uma transação ele
        # é ignorado em silêncio, e qualquer execução pelo SQLAlchemy abriria
        # uma. Foi exatamente o que aconteceu na primeira tentativa — migração
        # sem erro, sem efeito e com a tabela temporária do batch largada.
        if eh_sqlite:
            bruta = connection.connection
            bruta.rollback()
            cursor = bruta.cursor()
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.close()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=eh_sqlite,
        )
        with context.begin_transaction():
            context.run_migrations()
        if eh_sqlite:
            bruta = connection.connection
            cursor = bruta.cursor()
            orfas = cursor.execute("PRAGMA foreign_key_check").fetchall()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
            if orfas:
                raise RuntimeError(
                    f"Migração deixou {len(orfas)} referência(s) órfã(s): {orfas[:5]}")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
