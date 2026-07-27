"""Testes do CRUD de gestão de escalas (seção 4), protegido e auditado.

Cobre: proteção por login, criar escala com postos, alterar (incl. bloqueio de
zerar as duas cores — regra 4.5), extinção lógica (ativa=False — regra 8),
participação (adicionar/reativar/isentar — regra 3.3/7.6) e concorrência
(declarar/remover — regra 7.4.1), sempre conferindo o registro em `auditoria`.
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.escala import Escala, EscalaConcorrente, Participacao
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


def _nova_escala(**over):
    corpo = {
        "nome": "Oficial de Dia",
        "postos": [{"ordem": 1, "rotulo": "Posto 1"}],
    }
    corpo.update(over)
    return corpo


def _criar(client, auth, **over):
    return client.post("/api/escalas", json=_nova_escala(**over), headers=auth)


# --- proteção ---
def test_criar_sem_token_401(client):
    assert client.post("/api/escalas", json=_nova_escala()).status_code == 401


def test_consulta_segue_aberta(client, auth):
    _criar(client, auth)
    assert client.get("/api/escalas").status_code == 200


# --- criar ---
def test_criar_ok_com_postos_e_audita(client, db, auth):
    r = _criar(client, auth)
    assert r.status_code == 201
    body = r.json()
    assert body["nome"] == "Oficial de Dia"
    assert len(body["postos"]) == 1
    assert body["postos"][0]["ordem"] == 1

    reg = db.scalar(select(Auditoria).where(Auditoria.entidade == "escala"))
    assert reg.acao == "criar"
    assert reg.dados_depois["nome"] == "Oficial de Dia"
    # inicio_servico (time) precisa ter sido serializado em JSON
    assert reg.dados_depois["inicio_servico"] == "08:00:00"


def test_criar_sem_posto_422(client, auth):
    r = client.post("/api/escalas", json={"nome": "X", "postos": []}, headers=auth)
    assert r.status_code == 422


def test_criar_museu_so_vermelha(client, auth):
    r = _criar(client, auth, tem_preta=False, tem_vermelha=True)
    assert r.status_code == 201
    assert r.json()["tem_preta"] is False


def test_criar_sem_cor_422(client, auth):
    r = _criar(client, auth, tem_preta=False, tem_vermelha=False)
    assert r.status_code == 422


# --- alterar ---
def test_alterar_ok_e_audita(client, db, auth):
    eid = _criar(client, auth).json()["id"]
    r = client.patch(f"/api/escalas/{eid}", json={"nome": "OD"}, headers=auth)
    assert r.status_code == 200
    assert r.json()["nome"] == "OD"
    reg = db.scalar(select(Auditoria).where(Auditoria.acao == "alterar"))
    assert reg.dados_antes["nome"] == "Oficial de Dia"
    assert reg.dados_depois["nome"] == "OD"


def test_alterar_merge_zera_cores_422(client, auth):
    # museu (só vermelha); tirar a vermelha no PATCH deixaria sem nenhuma cor
    eid = _criar(client, auth, tem_preta=False, tem_vermelha=True).json()["id"]
    r = client.patch(f"/api/escalas/{eid}", json={"tem_vermelha": False}, headers=auth)
    assert r.status_code == 422


def test_alterar_inexistente_404(client, auth):
    assert client.patch("/api/escalas/999", json={"nome": "X"}, headers=auth).status_code == 404


# --- extinguir (lógico) ---
def test_extinguir_desativa_e_audita(client, db, auth):
    eid = _criar(client, auth).json()["id"]
    r = client.delete(f"/api/escalas/{eid}", headers=auth)
    assert r.status_code == 200
    assert r.json()["ativa"] is False
    assert db.get(Escala, eid) is not None   # exclusão lógica
    reg = db.scalar(select(Auditoria).where(Auditoria.acao == "excluir", Auditoria.entidade == "escala"))
    assert reg.dados_depois["ativa"] is False


# --- participação ---
def test_adicionar_participante_e_audita(client, db, auth):
    eid = _criar(client, auth).json()["id"]
    r = client.post(f"/api/escalas/{eid}/participacoes", json={"militar_id": 1}, headers=auth)
    assert r.status_code == 201
    assert r.json()["ativo"] is True
    reg = db.scalar(select(Auditoria).where(Auditoria.entidade == "participacao"))
    assert reg.acao == "criar"


def test_adicionar_militar_inexistente_422(client, auth):
    eid = _criar(client, auth).json()["id"]
    r = client.post(f"/api/escalas/{eid}/participacoes", json={"militar_id": 999}, headers=auth)
    assert r.status_code == 422


def test_adicionar_duplicado_409(client, auth):
    eid = _criar(client, auth).json()["id"]
    client.post(f"/api/escalas/{eid}/participacoes", json={"militar_id": 1}, headers=auth)
    r = client.post(f"/api/escalas/{eid}/participacoes", json={"militar_id": 1}, headers=auth)
    assert r.status_code == 409


def test_remover_participante_desativa(client, db, auth):
    eid = _criar(client, auth).json()["id"]
    client.post(f"/api/escalas/{eid}/participacoes", json={"militar_id": 1}, headers=auth)
    r = client.delete(f"/api/escalas/{eid}/participacoes/1", headers=auth)
    assert r.status_code == 200
    assert r.json()["ativo"] is False
    part = db.scalar(select(Participacao).where(Participacao.escala_id == eid, Participacao.militar_id == 1))
    assert part is not None and part.ativo is False


def test_readicionar_reativa(client, auth):
    eid = _criar(client, auth).json()["id"]
    client.post(f"/api/escalas/{eid}/participacoes", json={"militar_id": 1}, headers=auth)
    client.delete(f"/api/escalas/{eid}/participacoes/1", headers=auth)
    r = client.post(f"/api/escalas/{eid}/participacoes", json={"militar_id": 1}, headers=auth)
    assert r.status_code == 201
    assert r.json()["ativo"] is True


def test_remover_participante_inexistente_404(client, auth):
    eid = _criar(client, auth).json()["id"]
    assert client.delete(f"/api/escalas/{eid}/participacoes/1", headers=auth).status_code == 404


# --- concorrência ---
def test_declarar_concorrencia_normaliza_e_audita(client, db, auth):
    a = _criar(client, auth, nome="A").json()["id"]
    b = _criar(client, auth, nome="B").json()["id"]
    # declara na ordem maior->menor; o service normaliza para menor<maior
    r = client.post(f"/api/escalas/{b}/concorrentes", json={"escala_b_id": a}, headers=auth)
    assert r.status_code == 201
    body = r.json()
    assert (body["escala_menor_id"], body["escala_maior_id"]) == (min(a, b), max(a, b))
    assert db.get(EscalaConcorrente, (min(a, b), max(a, b))) is not None
    reg = db.scalar(select(Auditoria).where(Auditoria.entidade == "escala_concorrente"))
    assert reg.acao == "criar"


def test_declarar_consigo_mesma_422(client, auth):
    a = _criar(client, auth).json()["id"]
    r = client.post(f"/api/escalas/{a}/concorrentes", json={"escala_b_id": a}, headers=auth)
    assert r.status_code == 422


def test_declarar_com_inexistente_422(client, auth):
    a = _criar(client, auth).json()["id"]
    r = client.post(f"/api/escalas/{a}/concorrentes", json={"escala_b_id": 999}, headers=auth)
    assert r.status_code == 422


def test_remover_concorrencia_204(client, db, auth):
    a = _criar(client, auth, nome="A").json()["id"]
    b = _criar(client, auth, nome="B").json()["id"]
    client.post(f"/api/escalas/{a}/concorrentes", json={"escala_b_id": b}, headers=auth)
    r = client.delete(f"/api/escalas/{a}/concorrentes/{b}", headers=auth)
    assert r.status_code == 204
    assert db.get(EscalaConcorrente, (min(a, b), max(a, b))) is None
    reg = db.scalar(select(Auditoria).where(
        Auditoria.entidade == "escala_concorrente", Auditoria.acao == "excluir"))
    assert reg is not None
