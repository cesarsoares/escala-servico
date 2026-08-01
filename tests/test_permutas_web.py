"""Testes das telas de permuta (regra 9), protegidas por sessão (regra 11).

A lógica de negócio já é coberta por tests/test_permuta_service.py; aqui a
questão é a interface: a recusa (regra 10.5) precisa chegar ao gestor como
MENSAGEM, não como erro de sistema, e o registro precisa ser auditado.
"""
from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.domain.models import Cor
from app.main import app
from app.models.escala import Escala, Participacao, Posto
from app.models.gestao import Auditoria
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao, TipoImpedimento
from app.models.servico import Permuta, Servico
from app.seeds import seed_circulos, seed_postos_graduacao
from app.seeds.usuario import criar_ou_atualizar_gestor

DIA = date(2026, 8, 3)      # segunda-feira


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
    s.add(TipoImpedimento(id=1, nome="Dispensa"))
    criar_ou_atualizar_gestor(s, "brigada", "senha-boa-123", "Sgt Brigada")
    s.commit()
    yield s
    s.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def logado(client):
    client.post("/gestao/login", data={"username": "brigada", "password": "senha-boa-123"},
                follow_redirects=False)
    return client


def _militar(db, nome):
    m = Militar(nome_guerra=nome, nome_completo=f"{nome} de Tal", om_id=1,
                posto_graduacao_id=db.scalar(
                    select(PostoGraduacao.id).where(PostoGraduacao.sigla == "Cap")))
    db.add(m)
    db.flush()
    return m


def _servico(db, escala, militar, dia=DIA):
    s = Servico(escala_id=escala.id, posto_id=escala.postos[0].id, militar_id=militar.id,
                dia=dia, cor=Cor.VERMELHA if dia.weekday() >= 5 else Cor.PRETA,
                inicio_dt=datetime.combine(dia, time(8, 0)),
                termino_dt=datetime.combine(dia, time(8, 0)) + timedelta(hours=24))
    db.add(s)
    db.flush()
    return s


@pytest.fixture()
def cenario(db):
    """Escala de 1 posto com SILVA escalado e ROANA disponível para cobrir."""
    e = Escala(nome="Oficial de Dia", folga_minima_horas=48)
    e.postos = [Posto(ordem=1, rotulo="Serviço")]
    db.add(e)
    db.flush()
    silva, roana = _militar(db, "SILVA"), _militar(db, "ROANA")
    db.add_all([Participacao(militar_id=silva.id, escala_id=e.id),
                Participacao(militar_id=roana.id, escala_id=e.id)])
    s = _servico(db, e, silva)
    db.commit()
    return e, s, silva, roana


# --- proteção (regra 11) ---
def test_permutas_exige_sessao(client, cenario):
    r = client.get("/gestao/permutas", follow_redirects=False)
    assert r.status_code == 303 and "/gestao/login" in r.headers["location"]


def test_registrar_exige_sessao(client, cenario):
    _, s, _, roana = cenario
    r = client.post(f"/gestao/permutas/servico/{s.id}",
                    data={"militar_substituto_id": roana.id}, follow_redirects=False)
    assert r.status_code == 303 and "/gestao/login" in r.headers["location"]


# --- listagem do mês ---
def test_lista_o_mes_com_o_escalado(logado, cenario):
    escala, *_ = cenario
    r = logado.get(f"/gestao/permutas?escala_id={escala.id}&ano=2026&mes=8")
    assert r.status_code == 200
    assert "SILVA" in r.text
    assert "03/08" in r.text


def test_dia_da_semana_em_portugues(logado, cenario):
    """strftime('%a') sairia no locale do sistema ('Mon' no ambiente de dev)."""
    escala, *_ = cenario
    r = logado.get(f"/gestao/permutas?escala_id={escala.id}&ano=2026&mes=8")
    assert "seg" in r.text and "Mon" not in r.text


def test_mes_sem_servico_orienta(logado, cenario):
    escala, *_ = cenario
    r = logado.get(f"/gestao/permutas?escala_id={escala.id}&ano=2026&mes=12")
    assert "Nenhum serviço escalado neste mês" in r.text


# --- registro ---
def test_form_oferece_participantes_menos_o_escalado(logado, cenario):
    _, s, silva, roana = cenario
    r = logado.get(f"/gestao/permutas/servico/{s.id}")
    assert r.status_code == 200
    assert f'value="{roana.id}"' in r.text
    assert f'value="{silva.id}"' not in r.text     # o escalado não cobre a si mesmo


def test_registra_permuta_sem_mover_a_folga(logado, db, cenario):
    """Regra 9: o serviço (e a folga) continua do escalado."""
    escala, s, silva, roana = cenario
    r = logado.post(f"/gestao/permutas/servico/{s.id}",
                    data={"militar_substituto_id": str(roana.id), "observacao": "troca"},
                    follow_redirects=False)
    assert r.status_code == 303
    p = db.scalar(select(Permuta).where(Permuta.servico_id == s.id))
    assert p is not None and p.militar_substituto_id == roana.id
    assert db.get(Servico, s.id).militar_id == silva.id      # folga intacta
    assert p.observacao == "troca"


def test_registro_e_auditado_com_o_gestor(logado, db, cenario):
    _, s, _, roana = cenario
    logado.post(f"/gestao/permutas/servico/{s.id}",
                data={"militar_substituto_id": str(roana.id)}, follow_redirects=False)
    a = db.scalar(select(Auditoria).where(Auditoria.entidade == "permuta"))
    assert a is not None and a.acao == "criar"
    p = db.scalar(select(Permuta))
    assert p.autorizado_por is not None      # veio do gestor logado, não do form


def test_lista_mostra_a_cobertura_depois_de_registrar(logado, db, cenario):
    escala, s, _, roana = cenario
    logado.post(f"/gestao/permutas/servico/{s.id}",
                data={"militar_substituto_id": str(roana.id)}, follow_redirects=False)
    r = logado.get(f"/gestao/permutas?escala_id={escala.id}&ano=2026&mes=8")
    assert "coberto por" in r.text and "ROANA" in r.text


# --- recusas: chegam como mensagem, não como erro de sistema ---
def test_negada_por_impedimento_mostra_o_motivo(logado, db, cenario):
    _, s, _, roana = cenario
    db.add(Impedimento(militar_id=roana.id, tipo_impedimento_id=1, inicio=DIA, fim=DIA))
    db.commit()
    r = logado.post(f"/gestao/permutas/servico/{s.id}",
                    data={"militar_substituto_id": str(roana.id)})
    assert r.status_code == 400
    assert "Permuta negada" in r.text and "impedido" in r.text
    assert db.scalar(select(Permuta)) is None


def test_folga_minima_nao_barra_mais_a_permuta_pela_tela(logado, db, cenario):
    """Regra 10.5 reescrita em 01/08/2026: ROANA serviu na véspera e mesmo assim
    pode cobrir — cobrir não conta na folga de quem cobre."""
    escala, s, _, roana = cenario
    _servico(db, escala, roana, DIA - timedelta(days=1))
    db.commit()
    r = logado.post(f"/gestao/permutas/servico/{s.id}",
                    data={"militar_substituto_id": str(roana.id)}, follow_redirects=False)
    assert r.status_code == 303
    assert db.scalar(select(Permuta).where(Permuta.servico_id == s.id)) is not None


def test_negada_por_dobra_no_mesmo_dia_mostra_o_motivo(logado, db, cenario):
    """A guarda que fica de pé: ninguém cumpre dois serviços no mesmo dia."""
    escala, s, _, roana = cenario
    segundo = Posto(escala_id=escala.id, ordem=2, rotulo="2º posto")
    db.add(segundo)
    db.flush()
    db.add(Servico(escala_id=escala.id, posto_id=segundo.id, militar_id=roana.id,
                   dia=DIA, cor=Cor.PRETA,
                   inicio_dt=datetime.combine(DIA, time(8, 0)),
                   termino_dt=datetime.combine(DIA, time(8, 0)) + timedelta(hours=24)))
    db.commit()
    r = logado.post(f"/gestao/permutas/servico/{s.id}",
                    data={"militar_substituto_id": str(roana.id)})
    assert r.status_code == 400
    assert "Permuta negada" in r.text and "já está de serviço" in r.text
    assert db.scalar(select(Permuta).where(Permuta.servico_id == s.id)) is None


def test_sem_substituto_selecionado_avisa(logado, cenario):
    _, s, *_ = cenario
    r = logado.post(f"/gestao/permutas/servico/{s.id}", data={"militar_substituto_id": ""})
    assert r.status_code == 400 and "Selecione o substituto" in r.text


# --- cancelamento ---
def test_cancela_permuta_e_audita(logado, db, cenario):
    _, s, _, roana = cenario
    logado.post(f"/gestao/permutas/servico/{s.id}",
                data={"militar_substituto_id": str(roana.id)}, follow_redirects=False)
    r = logado.post(f"/gestao/permutas/servico/{s.id}/cancelar", follow_redirects=False)
    assert r.status_code == 303
    assert db.scalar(select(Permuta).where(Permuta.servico_id == s.id)) is None
    assert db.scalar(
        select(Auditoria).where(Auditoria.entidade == "permuta", Auditoria.acao == "excluir")
    ) is not None


def test_cancelar_sem_permuta_nao_quebra(logado, db, cenario):
    _, s, *_ = cenario
    r = logado.post(f"/gestao/permutas/servico/{s.id}/cancelar", follow_redirects=False)
    assert r.status_code == 303


def test_servico_inexistente_volta_para_a_lista(logado):
    r = logado.get("/gestao/permutas/servico/9999", follow_redirects=False)
    assert r.status_code == 303 and "/gestao/permutas" in r.headers["location"]
