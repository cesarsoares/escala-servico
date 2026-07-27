"""O SQLite precisa de PRAGMAs para se comportar como banco de produção.

O padrão do projeto é SQLite (uma instalação por OM, um escritor, muitos
leitores). Sem `foreign_keys=ON` — que o SQLite NÃO liga sozinho — as FKs
declaradas nos models viram decoração: dá para gravar serviço apontando para
militar inexistente. Este arquivo prende esse comportamento.
"""
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.database import Base
from app.models.escala import Escala, Participacao, Posto
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar
from app.models.servico import Servico
from app.seeds import seed_circulos, seed_postos_graduacao


@pytest.fixture()
def db(tmp_path):
    """Banco em ARQUIVO: o WAL não se aplica a :memory:."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'teste.sqlite3'}")
    Base.metadata.create_all(engine)
    s = Session(engine)
    seed_circulos(s)
    seed_postos_graduacao(s)
    s.add(OrganizacaoMilitar(id=1, nome="Quartel General", sigla="QG"))
    e = Escala(id=1, nome="Oficial de Dia", folga_minima_horas=24)
    e.postos = [Posto(id=1, ordem=1)]
    s.add(e)
    s.commit()
    yield s
    s.close()


def test_foreign_keys_estao_ligadas(db):
    assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_wal_ativo(db):
    """Leitor não bloqueia escritor — a consulta é aberta (regra 13.1)."""
    assert db.execute(text("PRAGMA journal_mode")).scalar() == "wal"


def test_servico_com_militar_inexistente_e_recusado(db):
    db.add(Servico(escala_id=1, posto_id=1, militar_id=999_999,
                   dia=date(2026, 1, 1), cor="preta",
                   inicio_dt=datetime(2026, 1, 1, 8), termino_dt=datetime(2026, 1, 2, 8)))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_cascade_apaga_participacao_do_militar_excluido(db):
    """ON DELETE CASCADE também depende do PRAGMA (a exclusão de militar no app
    é lógica, mas o vínculo precisa cair se a linha for removida de fato)."""
    m = Militar(nome_guerra="SILVA", nome_completo="Silva de Tal",
                posto_graduacao_id=7, om_id=1)
    db.add(m)
    db.flush()
    db.add(Participacao(militar_id=m.id, escala_id=1))
    db.commit()

    db.delete(m)
    db.commit()
    assert db.scalars(select(Participacao)).all() == []


def test_check_de_folga_minima_vale_no_sqlite(db):
    """Piso rígido de 24h (regra 7.2) — CHECK é aplicado pelo SQLite."""
    db.add(Escala(nome="Furada", folga_minima_horas=12))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
