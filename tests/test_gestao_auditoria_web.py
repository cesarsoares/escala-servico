"""Tela do histórico de alterações (HTML, protegida — regra 11).

A regra 11 exige histórico de TODAS as alterações manuais. Cobre os filtros, a
paginação, o diff campo a campo e a conversão do carimbo de tempo (o banco grava
em UTC; exibir cru adiantaria o relógio em 3h no horário de Brasília).
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.gestao import Auditoria, Usuario
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.seeds import seed_circulos, seed_postos_graduacao
from app.seeds.usuario import criar_ou_atualizar_gestor
from app.services import auditoria as auditoria_service
from app.web import hora_local


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = Session(engine)
    seed_circulos(s)
    seed_postos_graduacao(s)
    s.add(OrganizacaoMilitar(id=1, nome="Quartel General", sigla="QG"))
    criar_ou_atualizar_gestor(s, "brigada", "senha-boa-123", "Sgt Brigada")
    criar_ou_atualizar_gestor(s, "adjunto", "senha-boa-123", "Cb Adjunto")
    s.flush()
    sgt = s.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "2º Sgt"))
    s.add(Militar(id=1, nome_guerra="M1", nome_completo="Militar 1",
                  posto_graduacao_id=sgt, om_id=1))
    s.commit()
    yield s
    s.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        c.post("/gestao/login", data={"username": "brigada", "password": "senha-boa-123"},
               follow_redirects=False)
        yield c
    app.dependency_overrides.clear()


def _registro(db, **over):
    dados = {"usuario_id": 1, "entidade": "militar", "entidade_id": 1, "acao": "alterar",
             "dados_antes": {"nome_guerra": "M1", "cpf": None, "ativo": True},
             "dados_depois": {"nome_guerra": "SOUZA", "cpf": None, "ativo": True}}
    dados.update(over)
    reg = Auditoria(**dados)
    db.add(reg)
    db.commit()
    return reg


# --- diff (o que a tela acrescenta à API) ------------------------------------
def test_diferencas_lista_so_o_que_mudou():
    mudou = auditoria_service.diferencas(
        {"nome": "A", "cpf": None, "ativo": True},
        {"nome": "B", "cpf": None, "ativo": False})
    assert mudou == [("nome", "A", "B"), ("ativo", True, False)]


def test_diferencas_vazia_em_criar_e_excluir():
    """Em criar/excluir um dos lados não existe: a tela mostra o retrato inteiro."""
    assert auditoria_service.diferencas(None, {"nome": "A"}) == []
    assert auditoria_service.diferencas({"nome": "A"}, None) == []


def test_diferencas_pega_campo_que_so_existe_de_um_lado():
    assert auditoria_service.diferencas({"a": 1}, {"a": 1, "b": 2}) == [("b", None, 2)]


# --- horário (o banco grava UTC) ---------------------------------------------
def test_hora_local_converte_de_utc():
    """Exibir o valor cru adiantaria o relógio — defeito num log de auditoria."""
    utc = datetime(2026, 7, 25, 18, 50)          # como sai de func.now()
    esperado = utc.replace(tzinfo=timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M")
    assert hora_local(utc) == esperado
    assert hora_local(None) == "—"


def test_tela_mostra_o_horario_convertido(client, db):
    reg = _registro(db)
    reg.criado_em = datetime(2026, 7, 25, 18, 50)
    db.commit()
    r = client.get("/gestao/auditoria")
    assert hora_local(datetime(2026, 7, 25, 18, 50)) in r.text


# --- proteção e conteúdo ------------------------------------------------------
def test_exige_sessao(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as anonimo:
        r = anonimo.get("/gestao/auditoria", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/gestao/login"
    app.dependency_overrides.clear()


def test_lista_mostra_quem_o_que_e_a_mudanca(client, db):
    _registro(db)
    r = client.get("/gestao/auditoria")
    assert r.status_code == 200
    assert "Sgt Brigada" in r.text          # quem
    assert "militar" in r.text              # em quê
    assert "nome_guerra" in r.text          # o campo que mudou
    assert "SOUZA" in r.text                # o valor novo


def test_criar_mostra_o_retrato_gravado(client, db):
    _registro(db, acao="criar", dados_antes=None,
              dados_depois={"nome_guerra": "NOVO", "ativo": True})
    r = client.get("/gestao/auditoria")
    assert "estado gravado" in r.text and "NOVO" in r.text


def test_sem_registros_avisa(client):
    assert "Nenhuma alteração registrada" in client.get("/gestao/auditoria").text


# --- filtros ------------------------------------------------------------------
def test_filtra_por_entidade(client, db):
    _registro(db, entidade="militar")
    _registro(db, entidade="escala", entidade_id=7)
    r = client.get("/gestao/auditoria?entidade=escala")
    assert "escala" in r.text and "#7" in r.text
    assert "#1" not in r.text


def test_filtra_por_acao(client, db):
    _registro(db, acao="alterar")
    _registro(db, acao="excluir", entidade_id=42, dados_depois=None)
    r = client.get("/gestao/auditoria?acao=excluir")
    assert "#42" in r.text and "#1" not in r.text


def test_filtra_por_gestor(client, db):
    _registro(db, usuario_id=1)
    _registro(db, usuario_id=2, entidade_id=99)
    r = client.get("/gestao/auditoria?usuario_id=2")
    assert "Cb Adjunto" in r.text and "#99" in r.text
    assert "Sgt Brigada" not in r.text.split("<tbody>")[1]


def test_filtra_por_periodo(client, db):
    """A janela é escolhida em datas locais e a coluna é UTC — o dia inteiro
    precisa entrar, inclusive as horas que caem no outro lado da conversão."""
    antigo = _registro(db, entidade_id=10)
    recente = _registro(db, entidade_id=20)
    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    antigo.criado_em = agora - timedelta(days=10)
    recente.criado_em = agora
    db.commit()

    hoje = datetime.now().date().isoformat()
    r = client.get(f"/gestao/auditoria?desde={hoje}")
    assert "#20" in r.text and "#10" not in r.text


def test_filtro_invalido_nao_derruba_a_tela(client, db):
    _registro(db)
    r = client.get("/gestao/auditoria?desde=ontem&usuario_id=abc&ate=")
    assert r.status_code == 200      # data ilegível é ignorada, não vira 500


# --- paginação ----------------------------------------------------------------
def test_pagina_seguinte_so_aparece_quando_ha_mais(client, db):
    for i in range(51):
        _registro(db, entidade_id=1000 + i)
    r1 = client.get("/gestao/auditoria")
    assert "seguintes" in r1.text and "anteriores" not in r1.text
    r2 = client.get("/gestao/auditoria?pagina=2")
    assert "anteriores" in r2.text and "seguintes" not in r2.text


def test_paginacao_preserva_os_filtros(client, db):
    for i in range(51):
        _registro(db, entidade="escala", entidade_id=2000 + i)
    r = client.get("/gestao/auditoria?entidade=escala")
    # '&amp;' é a forma correta do & dentro de um href — o Jinja escapa, o
    # navegador desfaz. Procurar '&' cru aqui daria falso negativo.
    assert "pagina=2&amp;entidade=escala" in r.text


def test_pagina_zero_ou_negativa_422(client):
    assert client.get("/gestao/auditoria?pagina=0").status_code == 422
