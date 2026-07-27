"""Testes das rotas de consulta (abertas, sem login — regra 13.1).

TestClient sobre SQLite em memória, com get_db sobrescrito para a sessão do
teste. Cobre militares, escalas e a leitura da escala fechada (servicos).
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
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.seeds import seed_circulos, seed_postos_graduacao
from app.services import rotacao


@pytest.fixture()
def client():
    # StaticPool: uma única conexão em memória, compartilhada entre a thread do
    # teste e a thread que o TestClient usa para rodar o handler.
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    seed_circulos(db)
    seed_postos_graduacao(db)
    db.add(OrganizacaoMilitar(id=1, nome="Quartel General", sigla="QG"))
    db.flush()
    sgt = db.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "2º Sgt"))
    for i in (1, 2):
        db.add(Militar(
            id=i, nome_guerra=f"M{i}", nome_completo=f"Militar {i}",
            identidade=f"ID{i}", cpf=f"CPF{i}", posto_graduacao_id=sgt, om_id=1,
            data_promocao=date(2020, 1, 1), data_praca=date(2010, 1, 1),
            numero_antiguidade=100 - i * 10,
        ))
    db.add(Escala(id=1, nome="Oficial de Dia", folga_minima_horas=48))
    db.flush()
    db.add(Posto(escala_id=1, ordem=1, rotulo="Posto 1"))
    db.add(Participacao(militar_id=1, escala_id=1))
    db.add(Participacao(militar_id=2, escala_id=1))
    db.flush()
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 7, 20), date(2026, 7, 24))
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    db.close()


def test_listar_militares(client):
    r = client.get("/api/militares")
    assert r.status_code == 200
    nomes = [m["nome_guerra"] for m in r.json()]
    assert nomes == ["M1", "M2"]


def test_obter_militar_detalhe(client):
    r = client.get("/api/militares/1")
    assert r.status_code == 200
    body = r.json()
    assert body["posto_graduacao"]["sigla"] == "2º Sgt"
    assert body["om"]["sigla"] == "QG"
    assert "senha_hash" not in body   # militar nem tem; guarda de sanidade


def test_militar_inexistente_404(client):
    assert client.get("/api/militares/999").status_code == 404


def test_obter_escala_com_postos(client):
    r = client.get("/api/escalas/1")
    assert r.status_code == 200
    body = r.json()
    assert body["nome"] == "Oficial de Dia"
    assert len(body["postos"]) == 1
    assert body["postos"][0]["ordem"] == 1


def test_listar_servicos_do_periodo(client):
    r = client.get("/api/servicos", params={
        "inicio": "2026-07-20", "fim": "2026-07-24", "escala_id": 1,
    })
    assert r.status_code == 200
    servicos = r.json()
    # seg-sex, folga 48h, 2 militares: qua (22) fica sem ninguém disponível, então
    # só 4 serviços — M1(seg), M2(ter), M1(qui), M2(sex). É a folga funcionando.
    assert [(s["dia"], s["militar"]["nome_guerra"]) for s in servicos] == [
        ("2026-07-20", "M1"), ("2026-07-21", "M2"),
        ("2026-07-23", "M1"), ("2026-07-24", "M2"),
    ]
    assert all(s["cor"] == "preta" for s in servicos)


def test_servicos_periodo_invertido_422(client):
    r = client.get("/api/servicos", params={"inicio": "2026-07-24", "fim": "2026-07-20"})
    assert r.status_code == 422


def test_pagina_calendario_consulta(client):
    # tela aberta (regra 13.1): calendário mensal HTML; default = mês do serviço
    # mais recente (jul/2026, com M1/M2 escalados)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    html = r.text
    assert "Oficial de Dia" in html          # nome da escala no seletor
    assert "Julho" in html or "julho" in html
    assert "M1" in html and "M2" in html      # militares escalados no mês


def test_pagina_calendario_mes_explicito(client):
    r = client.get("/", params={"escala_id": 1, "ano": 2026, "mes": 7})
    assert r.status_code == 200
    assert "Julho / 2026" in r.text
