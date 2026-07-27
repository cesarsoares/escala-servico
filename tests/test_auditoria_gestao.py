"""Testes da leitura de auditoria (regra 11), protegida por login.

Gera histórico via mutações reais (criar militar, alterar, feriado) e confere os
filtros por entidade/registro, a paginação e a ordenação (mais recente primeiro).
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.seeds import seed_circulos, seed_postos_graduacao
from app.seeds.usuario import criar_ou_atualizar_gestor


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessao = Session(engine)
    seed_circulos(sessao)
    seed_postos_graduacao(sessao)
    sessao.add(OrganizacaoMilitar(id=1, nome="Quartel General", sigla="QG"))
    criar_ou_atualizar_gestor(sessao, "brigada", "senha-boa-123", "Sgt Brigada")
    sessao.commit()
    yield sessao
    sessao.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def auth(client):
    token = client.post(
        "/api/auth/login", data={"username": "brigada", "password": "senha-boa-123"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _cap_id(db):
    return db.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "Cap"))


def _gerar_historico(client, db, auth):
    """Cria militar (criar), altera (alterar) e cria feriado — 3 registros."""
    corpo = {
        "nome_guerra": "Silva", "nome_completo": "Fulano da Silva",
        "identidade": "0012345", "cpf": "00011122233",
        "posto_graduacao_id": _cap_id(db), "om_id": 1,
        "data_promocao": "2020-01-01", "data_praca": "2010-01-01",
    }
    mid = client.post("/api/militares", json=corpo, headers=auth).json()["id"]
    client.patch(f"/api/militares/{mid}", json={"nome_guerra": "Souza"}, headers=auth)
    client.post("/api/feriados", json={"data": "2026-12-25", "nome": "Natal"}, headers=auth)
    return mid


def test_auditoria_sem_token_401(client):
    assert client.get("/api/auditoria").status_code == 401


def test_lista_ordenada_mais_recente_primeiro(client, db, auth):
    _gerar_historico(client, db, auth)
    r = client.get("/api/auditoria", headers=auth)
    assert r.status_code == 200
    registros = r.json()
    assert len(registros) == 3
    # o último a ocorrer (feriado) vem primeiro
    assert registros[0]["entidade"] == "feriado"
    assert all(reg["usuario_id"] == 1 for reg in registros)


def test_filtro_por_entidade(client, db, auth):
    _gerar_historico(client, db, auth)
    r = client.get("/api/auditoria", params={"entidade": "militar"}, headers=auth)
    entidades = {reg["entidade"] for reg in r.json()}
    assert entidades == {"militar"}
    acoes = {reg["acao"] for reg in r.json()}
    assert acoes == {"criar", "alterar"}


def test_filtro_por_entidade_id(client, db, auth):
    mid = _gerar_historico(client, db, auth)
    r = client.get(
        "/api/auditoria",
        params={"entidade": "militar", "entidade_id": mid},
        headers=auth,
    )
    assert len(r.json()) == 2   # criar + alterar do mesmo militar


def test_filtro_por_usuario(client, db, auth):
    _gerar_historico(client, db, auth)
    r = client.get("/api/auditoria", params={"usuario_id": 1}, headers=auth)
    assert len(r.json()) == 3
    assert client.get("/api/auditoria", params={"usuario_id": 999}, headers=auth).json() == []


def test_paginacao_limite_offset(client, db, auth):
    _gerar_historico(client, db, auth)
    pagina1 = client.get("/api/auditoria", params={"limite": 2}, headers=auth).json()
    assert len(pagina1) == 2
    pagina2 = client.get("/api/auditoria", params={"limite": 2, "offset": 2}, headers=auth).json()
    assert len(pagina2) == 1
    ids1 = {r["id"] for r in pagina1}
    ids2 = {r["id"] for r in pagina2}
    assert ids1.isdisjoint(ids2)   # sem sobreposição entre páginas
