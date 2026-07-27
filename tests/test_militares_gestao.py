"""Testes do CRUD de gestão de militares (regra 3.2), protegido e auditado.

Cobre: proteção por login (401 sem token), criar/alterar/excluir com registro em
`auditoria`, exclusão lógica (ativo=False preservando o registro), conflito de
identidade/CPF (409), FK inexistente (422) e a regra 9.5 (nº de antiguidade só
para praças).
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.gestao import Auditoria
from app.models.militar import Militar
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


def _pg_id(db, sigla):
    return db.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == sigla))


def _novo(db, **over):
    corpo = {
        "nome_guerra": "Silva",
        "nome_completo": "Fulano da Silva",
        "identidade": "0012345",
        "cpf": "00011122233",
        "posto_graduacao_id": _pg_id(db, "Cap"),
        "om_id": 1,
        "data_promocao": "2020-01-01",
        "data_praca": "2010-01-01",
    }
    corpo.update(over)
    return corpo


# --- proteção (regra 11) ---
def test_criar_sem_token_401(client, db):
    assert client.post("/api/militares", json=_novo(db)).status_code == 401


def test_consulta_segue_aberta(client, db):
    # GET não exige token (regra 13.1) — não deve virar 401 ao adicionarmos gestão
    assert client.get("/api/militares").status_code == 200


# --- criar ---
def test_criar_ok_e_audita(client, db, auth):
    r = client.post("/api/militares", json=_novo(db), headers=auth)
    assert r.status_code == 201
    body = r.json()
    assert body["nome_guerra"] == "Silva"
    assert body["ativo"] is True

    reg = db.scalar(select(Auditoria).where(Auditoria.entidade == "militar"))
    assert reg.acao == "criar"
    assert reg.entidade_id == body["id"]
    assert reg.usuario_id == 1
    assert reg.dados_antes is None
    assert reg.dados_depois["nome_guerra"] == "Silva"


def test_criar_cpf_duplicado_409(client, db, auth):
    client.post("/api/militares", json=_novo(db), headers=auth)
    r = client.post("/api/militares", json=_novo(db, identidade="9999"), headers=auth)
    assert r.status_code == 409


def test_criar_om_inexistente_422(client, db, auth):
    r = client.post("/api/militares", json=_novo(db, om_id=999), headers=auth)
    assert r.status_code == 422


def test_antiguidade_so_praca_422(client, db, auth):
    # Cap é oficial → numero_antiguidade não se aplica (regra 9.5)
    r = client.post(
        "/api/militares", json=_novo(db, numero_antiguidade=5), headers=auth
    )
    assert r.status_code == 422


def test_antiguidade_praca_ok(client, db, auth):
    corpo = _novo(db, posto_graduacao_id=_pg_id(db, "2º Sgt"), numero_antiguidade=5)
    r = client.post("/api/militares", json=corpo, headers=auth)
    assert r.status_code == 201
    assert r.json()["numero_antiguidade"] == 5


# --- alterar ---
def test_alterar_ok_e_audita_antes_depois(client, db, auth):
    mid = client.post("/api/militares", json=_novo(db), headers=auth).json()["id"]
    r = client.patch(
        f"/api/militares/{mid}", json={"nome_guerra": "Souza"}, headers=auth
    )
    assert r.status_code == 200
    assert r.json()["nome_guerra"] == "Souza"

    reg = db.scalar(
        select(Auditoria).where(Auditoria.acao == "alterar")
    )
    assert reg.dados_antes["nome_guerra"] == "Silva"
    assert reg.dados_depois["nome_guerra"] == "Souza"


def test_alterar_inexistente_404(client, db, auth):
    assert client.patch(
        "/api/militares/999", json={"nome_guerra": "X"}, headers=auth
    ).status_code == 404


# --- excluir (lógico) ---
def test_excluir_desativa_preserva_e_audita(client, db, auth):
    mid = client.post("/api/militares", json=_novo(db), headers=auth).json()["id"]
    r = client.delete(f"/api/militares/{mid}", headers=auth)
    assert r.status_code == 200
    assert r.json()["ativo"] is False

    # o registro continua existindo (exclusão lógica)
    assert db.get(Militar, mid) is not None
    reg = db.scalar(select(Auditoria).where(Auditoria.acao == "excluir"))
    assert reg.dados_antes["ativo"] is True
    assert reg.dados_depois["ativo"] is False


def test_militar_inativo_some_da_consulta_padrao(client, db, auth):
    mid = client.post("/api/militares", json=_novo(db), headers=auth).json()["id"]
    client.delete(f"/api/militares/{mid}", headers=auth)
    ativos = client.get("/api/militares").json()
    assert all(m["id"] != mid for m in ativos)
    todos = client.get("/api/militares", params={"apenas_ativos": False}).json()
    assert any(m["id"] == mid for m in todos)
