"""Testes dos CRUDs de gestão de calendário (seção 5) e impedimento (regra 7.5).

Protegidos por login e auditados. Cobre feriado (criar/dup/alterar/excluir),
override de cor (upsert/redefinir/remover) e impedimento (criar com FK/período,
alterar revalidando período, excluir), sempre conferindo `auditoria`.
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.calendario import Feriado, OverrideDia
from app.models.gestao import Auditoria
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao, TipoImpedimento
from app.seeds import seed_circulos, seed_postos_graduacao, seed_tipos_impedimento
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
    seed_tipos_impedimento(sessao)
    sessao.add(OrganizacaoMilitar(id=1, nome="Quartel General", sigla="QG"))
    criar_ou_atualizar_gestor(sessao, "brigada", "senha-boa-123", "Sgt Brigada")
    sessao.flush()
    cap = sessao.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "Cap"))
    sessao.add(Militar(
        id=1, nome_guerra="Silva", nome_completo="Fulano da Silva",
        identidade="0012345", cpf="00011122233", posto_graduacao_id=cap, om_id=1,
        data_promocao=date(2020, 1, 1), data_praca=date(2010, 1, 1),
    ))
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


def _tipo_id(db):
    return db.scalar(select(TipoImpedimento.id).order_by(TipoImpedimento.id))


# --- proteção ---
def test_feriado_sem_token_401(client):
    assert client.post("/api/feriados", json={"data": "2026-12-25", "nome": "Natal"}).status_code == 401


def test_impedimento_sem_token_401(client):
    assert client.get("/api/impedimentos").status_code == 401


# --- feriado ---
def test_criar_feriado_e_audita(client, db, auth):
    r = client.post("/api/feriados", json={"data": "2026-12-25", "nome": "Natal"}, headers=auth)
    assert r.status_code == 201
    assert r.json()["nacional"] is False
    reg = db.scalar(select(Auditoria).where(Auditoria.entidade == "feriado"))
    assert reg.acao == "criar"
    assert reg.dados_depois["data"] == "2026-12-25"


def test_criar_feriado_data_duplicada_409(client, auth):
    client.post("/api/feriados", json={"data": "2026-12-25", "nome": "Natal"}, headers=auth)
    r = client.post("/api/feriados", json={"data": "2026-12-25", "nome": "Outro"}, headers=auth)
    assert r.status_code == 409


def test_listar_feriados_por_ano(client, auth):
    client.post("/api/feriados", json={"data": "2026-12-25", "nome": "Natal"}, headers=auth)
    client.post("/api/feriados", json={"data": "2027-01-01", "nome": "Ano Novo"}, headers=auth)
    r = client.get("/api/feriados", params={"ano": 2026}, headers=auth)
    assert [f["nome"] for f in r.json()] == ["Natal"]


def test_alterar_feriado_audita_antes_depois(client, db, auth):
    fid = client.post("/api/feriados", json={"data": "2026-12-25", "nome": "Natal"}, headers=auth).json()["id"]
    r = client.patch(f"/api/feriados/{fid}", json={"nome": "Natal (feriado)"}, headers=auth)
    assert r.status_code == 200
    reg = db.scalar(select(Auditoria).where(Auditoria.acao == "alterar", Auditoria.entidade == "feriado"))
    assert reg.dados_antes["nome"] == "Natal"
    assert reg.dados_depois["nome"] == "Natal (feriado)"


def test_excluir_feriado_204_e_audita(client, db, auth):
    fid = client.post("/api/feriados", json={"data": "2026-12-25", "nome": "Natal"}, headers=auth).json()["id"]
    r = client.delete(f"/api/feriados/{fid}", headers=auth)
    assert r.status_code == 204
    assert db.get(Feriado, fid) is None
    reg = db.scalar(select(Auditoria).where(Auditoria.acao == "excluir", Auditoria.entidade == "feriado"))
    assert reg.dados_antes["nome"] == "Natal"


# --- override de cor (regra 5.3) ---
def test_upsert_override_cria_e_redefine(client, db, auth):
    # cria (default vermelha)
    r = client.put("/api/overrides/2026-11-15", json={"data": "2026-11-15"}, headers=auth)
    assert r.status_code == 200
    assert r.json()["cor"] == "vermelha"
    # redefine para preta (feriado trabalhado — generalização 5.3)
    r2 = client.put(
        "/api/overrides/2026-11-15",
        json={"data": "2026-11-15", "cor": "preta", "observacao": "trabalhado"},
        headers=auth,
    )
    assert r2.status_code == 200
    assert r2.json()["cor"] == "preta"
    assert db.get(OverrideDia, date(2026, 11, 15)).cor.value == "preta"
    # auditoria: uma criação e uma alteração
    acoes = [a.acao for a in db.scalars(
        select(Auditoria).where(Auditoria.entidade == "override_dia").order_by(Auditoria.id))]
    assert acoes == ["criar", "alterar"]


def test_upsert_override_data_divergente_422(client, auth):
    r = client.put("/api/overrides/2026-11-15", json={"data": "2026-11-16"}, headers=auth)
    assert r.status_code == 422


def test_remover_override_204(client, db, auth):
    client.put("/api/overrides/2026-11-15", json={"data": "2026-11-15"}, headers=auth)
    r = client.delete("/api/overrides/2026-11-15", headers=auth)
    assert r.status_code == 204
    assert db.get(OverrideDia, date(2026, 11, 15)) is None


def test_remover_override_inexistente_404(client, auth):
    assert client.delete("/api/overrides/2030-01-01", headers=auth).status_code == 404


# --- impedimento (regra 7.5) ---
def test_criar_impedimento_e_audita(client, db, auth):
    corpo = {
        "militar_id": 1, "tipo_impedimento_id": _tipo_id(db),
        "inicio": "2026-08-01", "fim": "2026-08-10", "observacao": "férias",
    }
    r = client.post("/api/impedimentos", json=corpo, headers=auth)
    assert r.status_code == 201
    reg = db.scalar(select(Auditoria).where(Auditoria.entidade == "impedimento"))
    assert reg.acao == "criar"
    assert reg.dados_depois["inicio"] == "2026-08-01"


def test_criar_impedimento_periodo_invalido_422(client, db, auth):
    corpo = {"militar_id": 1, "tipo_impedimento_id": _tipo_id(db), "inicio": "2026-08-10", "fim": "2026-08-01"}
    assert client.post("/api/impedimentos", json=corpo, headers=auth).status_code == 422


def test_criar_impedimento_militar_inexistente_422(client, db, auth):
    corpo = {"militar_id": 999, "tipo_impedimento_id": _tipo_id(db), "inicio": "2026-08-01", "fim": "2026-08-10"}
    assert client.post("/api/impedimentos", json=corpo, headers=auth).status_code == 422


def test_criar_impedimento_tipo_inexistente_422(client, auth):
    corpo = {"militar_id": 1, "tipo_impedimento_id": 999, "inicio": "2026-08-01", "fim": "2026-08-10"}
    assert client.post("/api/impedimentos", json=corpo, headers=auth).status_code == 422


def test_alterar_impedimento_revalida_periodo_422(client, db, auth):
    corpo = {"militar_id": 1, "tipo_impedimento_id": _tipo_id(db), "inicio": "2026-08-01", "fim": "2026-08-10"}
    iid = client.post("/api/impedimentos", json=corpo, headers=auth).json()["id"]
    # empurra o fim para antes do inicio existente
    r = client.patch(f"/api/impedimentos/{iid}", json={"fim": "2026-07-01"}, headers=auth)
    assert r.status_code == 422


def test_alterar_impedimento_ok_e_audita(client, db, auth):
    corpo = {"militar_id": 1, "tipo_impedimento_id": _tipo_id(db), "inicio": "2026-08-01", "fim": "2026-08-10"}
    iid = client.post("/api/impedimentos", json=corpo, headers=auth).json()["id"]
    r = client.patch(f"/api/impedimentos/{iid}", json={"fim": "2026-08-15"}, headers=auth)
    assert r.status_code == 200
    assert r.json()["fim"] == "2026-08-15"
    reg = db.scalar(select(Auditoria).where(Auditoria.acao == "alterar", Auditoria.entidade == "impedimento"))
    assert reg.dados_antes["fim"] == "2026-08-10"
    assert reg.dados_depois["fim"] == "2026-08-15"


def test_listar_impedimentos_por_militar(client, db, auth):
    corpo = {"militar_id": 1, "tipo_impedimento_id": _tipo_id(db), "inicio": "2026-08-01", "fim": "2026-08-10"}
    client.post("/api/impedimentos", json=corpo, headers=auth)
    r = client.get("/api/impedimentos", params={"militar_id": 1}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["tipo"]["nome"]   # detalhe traz o tipo aninhado


def test_excluir_impedimento_204_e_audita(client, db, auth):
    corpo = {"militar_id": 1, "tipo_impedimento_id": _tipo_id(db), "inicio": "2026-08-01", "fim": "2026-08-10"}
    iid = client.post("/api/impedimentos", json=corpo, headers=auth).json()["id"]
    r = client.delete(f"/api/impedimentos/{iid}", headers=auth)
    assert r.status_code == 204
    assert db.get(Impedimento, iid) is None
    reg = db.scalar(select(Auditoria).where(Auditoria.acao == "excluir", Auditoria.entidade == "impedimento"))
    assert reg is not None
