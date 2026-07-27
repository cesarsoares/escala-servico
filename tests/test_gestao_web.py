"""Testes das telas de gestão (HTML, protegidas por cookie — regra 11).

Cobre login/logout, o redirecionamento de página protegida sem sessão, o painel,
a lista do efetivo e a escalação de período pela interface (que grava serviços e
audita).
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.escala import Escala, Participacao, Posto
from app.models.gestao import Auditoria
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao, TipoImpedimento
from app.models.servico import Servico
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
    s = Session(engine)
    seed_circulos(s)
    seed_postos_graduacao(s)
    seed_tipos_impedimento(s)
    s.add(OrganizacaoMilitar(id=1, nome="Quartel General", sigla="QG"))
    criar_ou_atualizar_gestor(s, "brigada", "senha-boa-123", "Sgt Brigada")
    s.flush()
    sgt = s.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "2º Sgt"))
    for i in (1, 2):
        s.add(Militar(id=i, nome_guerra=f"M{i}", nome_completo=f"Militar {i}",
                      posto_graduacao_id=sgt, om_id=1, numero_antiguidade=100 - i * 10))
    s.add(Escala(id=1, nome="Oficial de Dia", folga_minima_horas=48))
    s.flush()
    s.add(Posto(escala_id=1, ordem=1))
    s.add(Participacao(militar_id=1, escala_id=1))
    s.add(Participacao(militar_id=2, escala_id=1))
    s.commit()
    yield s
    s.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _login(client, senha="senha-boa-123"):
    return client.post("/gestao/login", data={"username": "brigada", "password": senha},
                       follow_redirects=True)


def test_login_pagina_ok(client):
    r = client.get("/gestao/login")
    assert r.status_code == 200 and "Gestão da escala" in r.text


def test_pagina_protegida_sem_sessao_redireciona(client):
    r = client.get("/gestao", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/gestao/login"


def test_login_ignora_espaco_no_nome_de_usuario(client):
    r = client.post("/gestao/login", data={"username": " brigada ", "password": "senha-boa-123"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/gestao"


def test_login_senha_errada(client):
    r = client.post("/gestao/login", data={"username": "brigada", "password": "x"},
                    follow_redirects=False)
    assert r.status_code == 401 and "inválidos" in r.text


def test_login_ok_abre_painel(client):
    r = _login(client)
    assert r.status_code == 200
    assert "Painel" in r.text
    assert "militares ativos" in r.text
    assert client.cookies.get("access_token")


def test_lista_efetivo(client):
    _login(client)
    r = client.get("/gestao/militares")
    assert r.status_code == 200
    assert "M1" in r.text and "M2" in r.text
    assert "2º Sgt" in r.text


def test_escalar_pela_interface_grava_e_audita(client, db):
    _login(client)
    r = client.post("/gestao/escalar", data={
        "escala_id": 1, "inicio": "2026-07-20", "fim": "2026-07-24",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert "serviços gravados" in r.text
    # gravou serviços e auditou a ação 'escalar'
    assert db.scalar(select(func.count()).select_from(Servico)) > 0
    assert db.scalar(select(Auditoria).where(Auditoria.acao == "escalar")) is not None


def test_escalar_periodo_invertido_400(client):
    _login(client)
    r = client.post("/gestao/escalar", data={
        "escala_id": 1, "inicio": "2026-07-24", "fim": "2026-07-20",
    })
    assert r.status_code == 400 and "anterior" in r.text


def test_logout_limpa_sessao(client):
    _login(client)
    client.get("/gestao/logout", follow_redirects=False)
    # sem cookie, painel volta a redirecionar
    client.cookies.clear()
    r = client.get("/gestao", follow_redirects=False)
    assert r.status_code == 303


# --- impedimentos ---
def _tipo(db):
    return db.scalar(select(TipoImpedimento.id).order_by(TipoImpedimento.id))


def test_impedimentos_form(client):
    _login(client)
    r = client.get("/gestao/impedimentos")
    assert r.status_code == 200 and "Impedimentos" in r.text and "M1" in r.text


def test_criar_impedimento_grava_e_audita(client, db):
    _login(client)
    r = client.post("/gestao/impedimentos", data={
        "militar_id": 1, "tipo_impedimento_id": _tipo(db),
        "inicio": "2026-08-01", "fim": "2026-08-10", "observacao": "férias",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert db.scalar(select(func.count()).select_from(Impedimento)) == 1
    assert db.scalar(select(Auditoria).where(
        Auditoria.entidade == "impedimento", Auditoria.acao == "criar")) is not None


def test_criar_impedimento_periodo_invalido_400(client, db):
    _login(client)
    r = client.post("/gestao/impedimentos", data={
        "militar_id": 1, "tipo_impedimento_id": _tipo(db),
        "inicio": "2026-08-10", "fim": "2026-08-01",
    })
    assert r.status_code == 400 and "anterior" in r.text


def test_remover_impedimento(client, db):
    _login(client)
    client.post("/gestao/impedimentos", data={
        "militar_id": 1, "tipo_impedimento_id": _tipo(db),
        "inicio": "2026-08-01", "fim": "2026-08-10",
    })
    iid = db.scalar(select(Impedimento.id))
    r = client.post(f"/gestao/impedimentos/{iid}/remover", follow_redirects=False)
    assert r.status_code == 303
    assert db.get(Impedimento, iid) is None


# --- atalho Efetivo -> impedimentos do militar (gestão da pessoa num lugar) ---
def test_efetivo_tem_atalho_para_os_impedimentos_do_militar(client):
    _login(client)
    r = client.get("/gestao/militares")
    assert '/gestao/impedimentos?militar_id=1' in r.text
    assert ">impedimentos</a>" in r.text


def test_atalho_abre_no_contexto_do_militar(client, db):
    _login(client)
    db.add(Impedimento(militar_id=2, tipo_impedimento_id=_tipo(db),
                       inicio=date(2026, 8, 1), fim=date(2026, 8, 10),
                       observacao="ferias-do-M2"))
    db.commit()
    r = client.get("/gestao/impedimentos?militar_id=1")
    assert r.status_code == 200
    assert "Impedimentos de" in r.text and "M1" in r.text
    # a lista traz só os dele: o impedimento do M2 não aparece
    assert "ferias-do-M2" not in r.text
    assert "não tem impedimento registrado" in r.text
    # e o formulário já vem apontando para ele
    assert '<option value="1" selected' in r.text.replace(">\n", ">")


def test_militar_inexistente_no_atalho_cai_na_lista_geral(client):
    _login(client)
    r = client.get("/gestao/impedimentos?militar_id=9999")
    assert r.status_code == 200 and "Impedimentos de" not in r.text


def test_gravar_no_contexto_volta_para_a_ficha_do_militar(client, db):
    _login(client)
    r = client.post("/gestao/impedimentos", follow_redirects=False, data={
        "militar_id": 1, "tipo_impedimento_id": _tipo(db), "contexto": "1",
        "inicio": "2026-08-01", "fim": "2026-08-10",
    })
    assert r.status_code == 303
    assert r.headers["location"] == "/gestao/impedimentos?militar_id=1&ok=impedimento-criado"


def test_gravar_sem_contexto_volta_para_a_lista_geral(client, db):
    _login(client)
    r = client.post("/gestao/impedimentos", follow_redirects=False, data={
        "militar_id": 1, "tipo_impedimento_id": _tipo(db), "contexto": "",
        "inicio": "2026-08-01", "fim": "2026-08-10",
    })
    assert r.headers["location"] == "/gestao/impedimentos?ok=impedimento-criado"


def test_erro_no_contexto_nao_perde_o_militar_em_foco(client, db):
    _login(client)
    r = client.post("/gestao/impedimentos", data={
        "militar_id": 1, "tipo_impedimento_id": _tipo(db), "contexto": "1",
        "inicio": "2026-08-10", "fim": "2026-08-01",     # invertido
    })
    assert r.status_code == 400
    assert "anterior" in r.text and "Impedimentos de" in r.text


def test_situacao_do_impedimento_em_curso(client, db):
    """O gestor precisa ver de relance o que está valendo hoje."""
    _login(client)
    hoje = date.today()
    db.add(Impedimento(militar_id=1, tipo_impedimento_id=_tipo(db),
                       inicio=hoje - timedelta(days=1), fim=hoje + timedelta(days=1)))
    db.add(Impedimento(militar_id=1, tipo_impedimento_id=_tipo(db),
                       inicio=hoje + timedelta(days=30), fim=hoje + timedelta(days=40)))
    db.commit()
    texto = client.get("/gestao/impedimentos?militar_id=1").text
    assert "em curso" in texto and "futuro" in texto


def test_impedimento_com_regravar_pula_militar(client, db):
    """História ponta-a-ponta: escala, impede M1 no período, regrava -> M1 some."""
    _login(client)
    payload = {"escala_id": 1, "inicio": "2026-07-20", "fim": "2026-07-24"}
    client.post("/gestao/escalar", data=payload)
    assert db.scalar(select(func.count()).select_from(Servico)
                     .where(Servico.militar_id == 1)) > 0   # M1 estava escalado

    # M1 de férias no período todo
    client.post("/gestao/impedimentos", data={
        "militar_id": 1, "tipo_impedimento_id": _tipo(db),
        "inicio": "2026-07-20", "fim": "2026-07-24",
    })
    # re-escala regravando o período
    client.post("/gestao/escalar", data={**payload, "regravar": "1"})

    no_periodo = select(func.count()).select_from(Servico).where(
        Servico.dia >= date(2026, 7, 20), Servico.dia <= date(2026, 7, 24))
    assert db.scalar(no_periodo.where(Servico.militar_id == 1)) == 0   # M1 pulado
    assert db.scalar(no_periodo) > 0                                    # M2 assumiu


# --- CRUD de militar ---
def _cap(db):
    return db.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "Cap"))


def test_novo_militar_form(client):
    _login(client)
    r = client.get("/gestao/militares/novo")
    assert r.status_code == 200 and "Novo militar" in r.text


def test_criar_militar_ok_e_audita(client, db):
    _login(client)
    r = client.post("/gestao/militares", data={
        "nome_guerra": "Souza", "nome_completo": "João de Souza",
        "posto_graduacao_id": _cap(db), "om_id": 1,
    }, follow_redirects=False)
    assert r.status_code == 303
    m = db.scalar(select(Militar).where(Militar.nome_guerra == "Souza"))
    assert m is not None and m.ativo is True
    assert db.scalar(select(Auditoria).where(
        Auditoria.entidade == "militar", Auditoria.acao == "criar")) is not None


def test_criar_militar_sem_nome_400(client, db):
    _login(client)
    r = client.post("/gestao/militares", data={
        "nome_guerra": "", "nome_completo": "", "posto_graduacao_id": _cap(db), "om_id": 1,
    })
    assert r.status_code == 400 and "obrigatório" in r.text


def test_criar_militar_antiguidade_em_oficial_400(client, db):
    # regra 9.5: numero_antiguidade só p/ praças; Cap é oficial
    _login(client)
    r = client.post("/gestao/militares", data={
        "nome_guerra": "Ofic", "nome_completo": "Oficial X",
        "posto_graduacao_id": _cap(db), "om_id": 1, "numero_antiguidade": "5",
    })
    assert r.status_code == 400 and "9.5" in r.text


def test_editar_militar_altera_e_audita(client, db):
    _login(client)
    r = client.post("/gestao/militares/1", data={
        "nome_guerra": "M1", "nome_completo": "Militar Um Completo",
        "posto_graduacao_id": db.scalar(select(Militar.posto_graduacao_id).where(Militar.id == 1)),
        "om_id": 1,
    }, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Militar, 1).nome_completo == "Militar Um Completo"
    assert db.scalar(select(Auditoria).where(
        Auditoria.entidade == "militar", Auditoria.acao == "alterar")) is not None


def test_desativar_e_reativar_militar(client, db):
    _login(client)
    client.post("/gestao/militares/1/desativar", follow_redirects=False)
    db.expire_all()
    assert db.get(Militar, 1).ativo is False
    # some da lista padrão, aparece na de inativos
    assert "M1" not in client.get("/gestao/militares").text
    assert "M1" in client.get("/gestao/militares?inativos=1").text
    client.post("/gestao/militares/1/reativar", follow_redirects=False)
    db.expire_all()
    assert db.get(Militar, 1).ativo is True
