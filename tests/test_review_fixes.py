"""Regressões dos achados do /code-review (2026-07-24).

#1 PATCH de numero_antiguidade num oficial deve ser barrado (regra 9.5).
#3 Redeclarar concorrência já existente não gera auditoria 'criar' falsa.
#4 Filtro de auditoria tz-aware normaliza e filtra corretamente.
#5 Trava de escalação respeita o limite anunciado (sem off-by-one).
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.escala import Escala, Participacao, Posto
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
    # oficial (Cap): circulo.eh_praca = False -> não pode ter numero_antiguidade
    sessao.add(Militar(
        id=1, nome_guerra="Silva", nome_completo="Fulano da Silva",
        identidade="0012345", cpf="00011122233", posto_graduacao_id=cap, om_id=1,
        data_promocao=date(2020, 1, 1), data_praca=date(2010, 1, 1),
    ))
    sessao.add(Escala(id=1, nome="A", folga_minima_horas=24))
    sessao.add(Escala(id=2, nome="B", folga_minima_horas=24))
    sessao.flush()
    sessao.add(Posto(escala_id=1, ordem=1))
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


# --- #1: bypass da regra 9.5 no PATCH ---
def test_patch_antiguidade_em_oficial_barrado(client, auth):
    # PATCH só com numero_antiguidade (sem posto): antes escapava da checagem 9.5
    r = client.patch("/api/militares/1", json={"numero_antiguidade": 5}, headers=auth)
    assert r.status_code == 422


# --- #3: auditoria falsa em concorrência redeclarada ---
def test_redeclarar_concorrencia_nao_audita_de_novo(client, db, auth):
    client.post("/api/escalas/1/concorrentes", json={"escala_b_id": 2}, headers=auth)
    client.post("/api/escalas/1/concorrentes", json={"escala_b_id": 2}, headers=auth)  # de novo
    # ordem inversa também aponta para o mesmo par (menor<maior)
    client.post("/api/escalas/2/concorrentes", json={"escala_b_id": 1}, headers=auth)
    regs = db.scalars(
        select(Auditoria).where(Auditoria.entidade == "escala_concorrente")
    ).all()
    assert len(regs) == 1   # só a primeira declaração audita


# --- #4: filtro de auditoria tz-aware ---
def test_filtro_auditoria_tz_aware(client, db, auth):
    # gera 1 registro de auditoria
    client.post("/api/escalas/1/concorrentes", json={"escala_b_id": 2}, headers=auth)
    ate_futuro = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    desde_passado = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    r = client.get(
        "/api/auditoria",
        params={"desde": desde_passado, "ate": ate_futuro},
        headers=auth,
    )
    assert r.status_code == 200
    assert len(r.json()) == 1   # o registro cai dentro da janela aware normalizada
    # janela toda no passado não deve trazer nada
    r2 = client.get(
        "/api/auditoria",
        params={"ate": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()},
        headers=auth,
    )
    assert r2.json() == []


# --- #5: off-by-one da trava de escalação ---
def test_escalar_periodo_excede_limite_422(client, auth):
    # 367 dias inclusive (diff .days = 366) deve ser rejeitado (limite 366)
    r = client.post(
        "/api/escalas/1/escalar",
        json={"inicio": "2025-01-01", "fim": "2026-01-02"},
        headers=auth,
    )
    assert r.status_code == 422
    assert "366" in r.json()["detail"]
