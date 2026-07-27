"""Sessão e engine do banco (SQLAlchemy 2.0).

O banco padrão é **SQLite** — uma instalação por OM, um escritor (o gestor) e
muitos leitores (a consulta é aberta). O volume é pequeno: uma OM gera alguns
milhares de serviços por ano. PostgreSQL continua suportado: basta apontar
`DATABASE_URL` para ele (ver docker-compose.postgres.yml).

O SQLite exige três PRAGMAs por conexão para se comportar como esperado —
sem eles, as FKs declaradas nos models são decorativas.
"""
import sqlite3

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


# Registrado na classe Engine (não na instância) para valer também nas engines
# que os testes e as migrações criam — assim a suíte tem o mesmo rigor da
# produção. Em PostgreSQL o listener sai na primeira linha.
@event.listens_for(Engine, "connect")
def _pragmas_sqlite(dbapi_connection, connection_record):
    """Ajusta cada conexão SQLite.

    - foreign_keys: o SQLite nasce com as FKs DESLIGADAS; sem isto ele aceita
      serviço apontando para militar inexistente e ignora ON DELETE CASCADE.
    - journal_mode=WAL: leitor não bloqueia escritor. É o que mantém a consulta
      aberta respondendo enquanto o gestor escala um período inteiro.
    - busy_timeout: se outra escrita estiver em curso, espera em vez de falhar
      na hora com "database is locked".
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


engine = create_engine(settings.database_url, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base para todos os modelos ORM (ver app/models/)."""


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
