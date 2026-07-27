"""Testes das ações de gestão: escalação de período (regras 6/7) e permuta (regra 9).

Protegidas por login e auditadas. A escalação dispara o motor e grava os
serviços; a permuta anota a cobertura sem mover a folga (regra 9), com as guardas
mapeadas para 404 (não existe) / 409 (regra de negócio).
"""
from datetime import date

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
from app.models.servico import Permuta, Servico
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
    sgt = sessao.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "2º Sgt"))
    # 3 praças (numero_antiguidade: maior = mais moderno = topo da fila)
    for i, antg in ((1, 30), (2, 20), (3, 10)):
        sessao.add(Militar(
            id=i, nome_guerra=f"M{i}", nome_completo=f"Militar {i}",
            identidade=f"ID{i}", cpf=f"CPF{i}", posto_graduacao_id=sgt, om_id=1,
            data_promocao=date(2020, 1, 1), data_praca=date(2010, 1, 1),
            numero_antiguidade=antg,
        ))
    # escala com folga 24h (piso) e 1 posto -> rotação diária entre os 3
    sessao.add(Escala(id=1, nome="Oficial de Dia", folga_minima_horas=24))
    sessao.flush()
    sessao.add(Posto(escala_id=1, ordem=1, rotulo="Posto 1"))
    for i in (1, 2, 3):
        sessao.add(Participacao(militar_id=i, escala_id=1))
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


# --- escalação de período ---
def test_escalar_sem_token_401(client):
    r = client.post("/api/escalas/1/escalar", json={"inicio": "2026-07-20", "fim": "2026-07-22"})
    assert r.status_code == 401


def test_escalar_ok_grava_e_audita(client, db, auth):
    # 2026-07-20..22 = seg,ter,qua (dias úteis -> preta)
    r = client.post("/api/escalas/1/escalar", json={"inicio": "2026-07-20", "fim": "2026-07-22"}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert len(body["dias"]) == 3
    assert all(d["cor"] == "preta" for d in body["dias"])
    assert body["dias_com_alerta"] == 0

    servicos = db.scalars(select(Servico).order_by(Servico.dia)).all()
    assert len(servicos) == 3                       # 1 posto/dia, 3 militares distintos
    assert len({s.militar_id for s in servicos}) == 3

    reg = db.scalar(select(Auditoria).where(Auditoria.acao == "escalar"))
    assert reg.entidade == "escala" and reg.entidade_id == 1
    assert reg.dados_depois["dias"] == 3
    assert reg.dados_depois["servicos_intencionados"] == 3


def test_escalar_periodo_invertido_422(client, auth):
    r = client.post("/api/escalas/1/escalar", json={"inicio": "2026-07-22", "fim": "2026-07-20"}, headers=auth)
    assert r.status_code == 422


def test_escalar_escala_inexistente_404(client, auth):
    r = client.post("/api/escalas/999/escalar", json={"inicio": "2026-07-20", "fim": "2026-07-22"}, headers=auth)
    assert r.status_code == 404


def test_escalar_idempotente(client, db, auth):
    payload = {"inicio": "2026-07-20", "fim": "2026-07-22"}
    client.post("/api/escalas/1/escalar", json=payload, headers=auth)
    client.post("/api/escalas/1/escalar", json=payload, headers=auth)   # de novo
    servicos = db.scalars(select(Servico)).all()
    assert len(servicos) == 3   # não duplicou (uq_servico_posto_dia)


def test_escalar_efetivo_insuficiente_alerta(client, db, auth):
    # escala só com 1 participante e folga 48h -> a terça fica sem ninguém (regra 7.8)
    db.add(Escala(id=2, nome="Solo", folga_minima_horas=48))
    db.flush()
    db.add(Posto(escala_id=2, ordem=1, rotulo="Posto 1"))
    db.add(Participacao(militar_id=1, escala_id=2))
    db.commit()
    r = client.post("/api/escalas/2/escalar", json={"inicio": "2026-07-20", "fim": "2026-07-22"}, headers=auth)
    assert r.status_code == 200
    assert r.json()["dias_com_alerta"] >= 1


# --- permuta ---
def _escala_fechada(client, auth):
    client.post("/api/escalas/1/escalar", json={"inicio": "2026-07-20", "fim": "2026-07-22"}, headers=auth)


def test_registrar_permuta_ok_sem_mover_folga_e_audita(client, db, auth):
    _escala_fechada(client, auth)
    s0 = db.scalars(select(Servico).order_by(Servico.dia)).first()
    escalado = s0.militar_id
    substituto = next(i for i in (1, 2, 3) if i != escalado)

    r = client.post("/api/permutas", json={
        "servico_id": s0.id, "militar_substituto_id": substituto, "observacao": "troca",
    }, headers=auth)
    assert r.status_code == 201
    body = r.json()
    assert body["militar_substituto_id"] == substituto
    assert body["autorizado_por"] == 1        # gestor logado, não veio do corpo

    # a folga não muda de dono: o serviço segue com o escalado (regra 9)
    db.refresh(s0)
    assert s0.militar_id == escalado

    reg = db.scalar(select(Auditoria).where(Auditoria.entidade == "permuta", Auditoria.acao == "criar"))
    assert reg.dados_depois["militar_substituto_id"] == substituto


def test_registrar_permuta_proprio_militar_409(client, db, auth):
    _escala_fechada(client, auth)
    s0 = db.scalars(select(Servico).order_by(Servico.dia)).first()
    r = client.post("/api/permutas", json={
        "servico_id": s0.id, "militar_substituto_id": s0.militar_id,
    }, headers=auth)
    assert r.status_code == 409


def test_registrar_permuta_ja_permutado_409(client, db, auth):
    _escala_fechada(client, auth)
    s0 = db.scalars(select(Servico).order_by(Servico.dia)).first()
    sub = next(i for i in (1, 2, 3) if i != s0.militar_id)
    client.post("/api/permutas", json={"servico_id": s0.id, "militar_substituto_id": sub}, headers=auth)
    r = client.post("/api/permutas", json={"servico_id": s0.id, "militar_substituto_id": sub}, headers=auth)
    assert r.status_code == 409


def test_registrar_permuta_servico_inexistente_404(client, auth):
    r = client.post("/api/permutas", json={"servico_id": 9999, "militar_substituto_id": 1}, headers=auth)
    assert r.status_code == 404


def test_cancelar_permuta_204_e_audita(client, db, auth):
    _escala_fechada(client, auth)
    s0 = db.scalars(select(Servico).order_by(Servico.dia)).first()
    sub = next(i for i in (1, 2, 3) if i != s0.militar_id)
    client.post("/api/permutas", json={"servico_id": s0.id, "militar_substituto_id": sub}, headers=auth)

    r = client.delete(f"/api/permutas/{s0.id}", headers=auth)
    assert r.status_code == 204
    assert db.scalar(select(Permuta).where(Permuta.servico_id == s0.id)) is None
    reg = db.scalar(select(Auditoria).where(Auditoria.entidade == "permuta", Auditoria.acao == "excluir"))
    assert reg is not None


def test_cancelar_permuta_inexistente_404(client, db, auth):
    _escala_fechada(client, auth)
    s0 = db.scalars(select(Servico).order_by(Servico.dia)).first()
    assert client.delete(f"/api/permutas/{s0.id}", headers=auth).status_code == 404
