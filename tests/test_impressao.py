"""Testes do documento da escala para publicação (regra 12).

A página é aberta como o resto da consulta (regra 13.1) e mostra quem serve em
cada dia. Ponto sensível coberto aqui: havendo permuta o documento exibe o
substituto SEM tirar o serviço do escalado — a folga é dele (regra 9).
"""
from datetime import date, datetime, time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.domain.models import Cor
from app.main import app
from app.models.calendario import Feriado
from app.models.escala import Escala, Participacao, Posto
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.models.servico import Permuta, Servico
from app.seeds import seed_circulos, seed_postos_graduacao


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
    sessao.commit()
    yield sessao
    sessao.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _cap(db):
    return db.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "Cap"))


def _militar(db, nome):
    m = Militar(nome_guerra=nome, nome_completo=f"{nome} de Tal",
                posto_graduacao_id=_cap(db), om_id=1)
    db.add(m)
    db.flush()
    return m


def _escala(db, nome="Oficial de Dia", rotulos=("Serviço",)):
    e = Escala(nome=nome, inicio_servico=time(8, 0), duracao_horas=24,
               folga_minima_horas=48)
    e.postos = [Posto(ordem=i + 1, rotulo=r) for i, r in enumerate(rotulos)]
    db.add(e)
    db.flush()
    return e


def _servico(db, escala, militar, dia: date, posto=None):
    s = Servico(escala_id=escala.id, posto_id=(posto or escala.postos[0]).id,
                militar_id=militar.id, dia=dia,
                cor=Cor.VERMELHA if dia.weekday() >= 5 else Cor.PRETA,
                inicio_dt=datetime.combine(dia, time(8, 0)),
                termino_dt=datetime.combine(dia, time(8, 0)))
    db.add(s)
    db.flush()
    return s


@pytest.fixture()
def cenario(db):
    """Uma escala de 1 posto, com um dia útil (seg) e um sábado."""
    e = _escala(db)
    silva, costa = _militar(db, "SILVA"), _militar(db, "COSTA")
    db.add_all([Participacao(militar_id=silva.id, escala_id=e.id),
                Participacao(militar_id=costa.id, escala_id=e.id)])
    _servico(db, e, silva, date(2026, 8, 3))    # segunda -> preta
    _servico(db, e, costa, date(2026, 8, 8))    # sábado  -> vermelha
    db.commit()
    return e, silva, costa


def test_documento_lista_os_dias_com_militar(client, cenario):
    escala, *_ = cenario
    r = client.get(f"/escalas/{escala.id}/impressao?ano=2026&mes=8")
    assert r.status_code == 200
    assert "Cap SILVA (QG)" in r.text
    assert "Cap COSTA (QG)" in r.text
    assert "Escala de serviço — Oficial de Dia" in r.text
    assert "agosto de 2026" in r.text.lower()


def test_consulta_aberta_sem_login(client, cenario):
    """Regra 13.1: o documento publicado não exige sessão."""
    escala, *_ = cenario
    assert client.get(f"/escalas/{escala.id}/impressao?ano=2026&mes=8").status_code == 200


def test_dia_vermelho_marcado_sem_depender_de_cor(client, cenario):
    """Impressora monocromática: sábado precisa da letra V, não só do fundo."""
    escala, *_ = cenario
    linhas = client.get(f"/escalas/{escala.id}/impressao?ano=2026&mes=8").text
    assert "vermelha" in linhas          # classe da linha do sábado
    assert ">V<" in linhas.replace(" ", "").replace("\n", "")


def test_feriado_recebe_asterisco(client, db):
    e = _escala(db)
    m = _militar(db, "ROANA")
    db.add(Feriado(data=date(2026, 9, 7), nome="Independência", nacional=True))
    _servico(db, e, m, date(2026, 9, 7))
    db.commit()
    texto = client.get(f"/escalas/{e.id}/impressao?ano=2026&mes=9").text
    assert "V*" in texto.replace(" ", "").replace("\n", "")


def test_permuta_mostra_substituto_sem_tirar_o_escalado(client, db):
    """Regra 9: quem cobre aparece, mas o serviço (e a folga) continua do escalado."""
    e = _escala(db)
    silva, roana = _militar(db, "SILVA"), _militar(db, "ROANA")
    s = _servico(db, e, silva, date(2026, 8, 3))
    db.add(Permuta(servico_id=s.id, militar_substituto_id=roana.id))
    db.commit()
    texto = client.get(f"/escalas/{e.id}/impressao?ano=2026&mes=8").text
    assert "Cap SILVA (QG)" in texto
    assert "permuta: Cap ROANA (QG)" in texto


def test_posto_unico_nao_mostra_a_coluna_posto(client, cenario):
    """Escala de uma vaga: a coluna repetiria o mesmo rótulo em toda linha."""
    escala, *_ = cenario
    texto = client.get(f"/escalas/{escala.id}/impressao?ano=2026&mes=8").text
    assert "<th class=\"c-posto\">Posto</th>" not in texto


def test_varios_postos_no_mesmo_dia(client, db):
    """Escala de guarda: o dia agrupa as vagas, cada uma com seu rótulo."""
    e = _escala(db, nome="Guarda", rotulos=("Comandante da Guarda", "Cabo da Guarda"))
    a, b = _militar(db, "ALFA"), _militar(db, "BRAVO")
    _servico(db, e, a, date(2026, 8, 3), posto=e.postos[0])
    _servico(db, e, b, date(2026, 8, 3), posto=e.postos[1])
    db.commit()
    texto = client.get(f"/escalas/{e.id}/impressao?ano=2026&mes=8").text
    assert "Comandante da Guarda" in texto and "Cabo da Guarda" in texto
    assert 'rowspan="2"' in texto


def test_mes_sem_servico_nao_quebra(client, cenario):
    escala, *_ = cenario
    r = client.get(f"/escalas/{escala.id}/impressao?ano=2026&mes=12")
    assert r.status_code == 200
    assert "Nenhum serviço escalado" in r.text


def test_escala_inexistente_404(client):
    assert client.get("/escalas/999/impressao?ano=2026&mes=8").status_code == 404


def test_mes_default_e_o_do_ultimo_servico(client, cenario):
    """Sem ano/mês na URL, abre onde a escala tem dados (como o calendário)."""
    escala, *_ = cenario
    r = client.get(f"/escalas/{escala.id}/impressao")
    assert r.status_code == 200
    assert "agosto de 2026" in r.text.lower()


def test_calendario_tem_link_para_a_impressao(client, cenario):
    escala, *_ = cenario
    r = client.get(f"/?escala_id={escala.id}&ano=2026&mes=8")
    assert f"/escalas/{escala.id}/impressao?ano=2026&mes=8" in r.text
