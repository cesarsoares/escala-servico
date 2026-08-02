"""Telas de resolução de conflito serviço × impedimento (demanda do Brigada, 30/07).

A regra e as guardas são cobertas por tests/test_conflitos.py. Aqui a questão é a
interface: o gestor que lança um impedimento sobre dias já escalados precisa ser
LEVADO à correção (o silêncio é o defeito relatado), a recusa precisa chegar como
mensagem e não como 500, e toda troca precisa ficar na auditoria (regra 11).
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

DIA = date(2026, 8, 3)      # segunda-feira -> preta


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


def _impede(db, militar, inicio=DIA, fim=DIA):
    imp = Impedimento(militar_id=militar.id, tipo_impedimento_id=1,
                      inicio=inicio, fim=fim, observacao="dispensa")
    db.add(imp)
    db.flush()
    return imp


@pytest.fixture()
def cenario(db):
    """SILVA escalado no dia 03/08; ROANA participante e livre."""
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


# --- proteção ---------------------------------------------------------------

def test_conflitos_exige_login(client):
    r = client.get("/gestao/conflitos", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/gestao/login" in r.headers["location"]


def test_substituir_exige_login(client, cenario):
    _, s, _, roana = cenario
    r = client.post(f"/gestao/conflitos/servico/{s.id}/substituir",
                    data={"militar_id": roana.id}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/gestao/login" in r.headers["location"]


# --- lista ------------------------------------------------------------------

def test_lista_vazia_diz_que_esta_tudo_certo(logado, cenario):
    r = logado.get("/gestao/conflitos")
    assert r.status_code == 200
    assert "Nenhum conflito" in r.text


def test_lista_mostra_o_conflito_e_o_substituto_proposto(logado, db, cenario):
    _, _, silva, roana = cenario
    _impede(db, silva)
    db.commit()
    r = logado.get("/gestao/conflitos")
    assert r.status_code == 200
    assert "SILVA" in r.text
    assert "ROANA" in r.text          # proposto pela fila
    assert "Dispensa" in r.text


def test_data_ilegivel_no_filtro_e_ignorada(logado, db, cenario):
    """Como no histórico: filtro ilegível não pode virar 500."""
    _, _, silva, _ = cenario
    _impede(db, silva)
    db.commit()
    assert logado.get("/gestao/conflitos?de=trinta-de-julho").status_code == 200


def test_filtro_por_militar_inexistente_nao_estoura(logado, cenario):
    assert logado.get("/gestao/conflitos?militar_id=9999").status_code == 200


# --- a troca ----------------------------------------------------------------

def test_trocar_o_escalado_grava_e_audita(logado, db, cenario):
    _, s, silva, roana = cenario
    _impede(db, silva)
    db.commit()

    r = logado.post(f"/gestao/conflitos/servico/{s.id}/substituir",
                    data={"militar_id": roana.id}, follow_redirects=False)
    assert r.status_code == 303
    assert "ok=conflito-resolvido" in r.headers["location"]

    db.expire_all()
    assert db.get(Servico, s.id).militar_id == roana.id
    reg = db.scalar(select(Auditoria).where(Auditoria.entidade == "servico",
                                            Auditoria.acao == "substituir"))
    assert reg is not None
    assert reg.dados_antes["militar_id"] == silva.id
    assert reg.dados_depois["militar_id"] == roana.id


def test_trocar_mantem_posto_dia_e_janela(logado, db, cenario):
    _, s, silva, roana = cenario
    posto, dia, inicio = s.posto_id, s.dia, s.inicio_dt
    _impede(db, silva)
    db.commit()
    logado.post(f"/gestao/conflitos/servico/{s.id}/substituir",
                data={"militar_id": roana.id}, follow_redirects=False)
    db.expire_all()
    novo = db.get(Servico, s.id)
    assert (novo.posto_id, novo.dia, novo.inicio_dt) == (posto, dia, inicio)


def test_recusa_chega_como_mensagem_nao_como_500(logado, db, cenario):
    """Substituto também impedido: 400 com o motivo, e nada muda no banco."""
    _, s, silva, roana = cenario
    _impede(db, silva)
    _impede(db, roana)
    db.commit()
    r = logado.post(f"/gestao/conflitos/servico/{s.id}/substituir",
                    data={"militar_id": roana.id})
    assert r.status_code == 400
    assert "impedido" in r.text
    db.expire_all()
    assert db.get(Servico, s.id).militar_id == silva.id


def test_recusa_de_quem_nao_participa(logado, db, cenario):
    _, s, silva, _ = cenario
    forasteiro = _militar(db, "ALHEIO")
    _impede(db, silva)
    db.commit()
    r = logado.post(f"/gestao/conflitos/servico/{s.id}/substituir",
                    data={"militar_id": forasteiro.id})
    assert r.status_code == 400
    assert "participante" in r.text


def test_servico_inexistente_devolve_404_com_a_tela(logado, cenario):
    r = logado.post("/gestao/conflitos/servico/999999/substituir", data={"militar_id": 1})
    assert r.status_code == 404
    assert "não encontrado" in r.text


def test_filtros_sobrevivem_a_troca(logado, db, cenario):
    """Resolver a partir da ficha de um militar volta para a MESMA lista filtrada."""
    _, s, silva, roana = cenario
    _impede(db, silva)
    db.commit()
    r = logado.post(
        f"/gestao/conflitos/servico/{s.id}/substituir?militar_id={silva.id}&de={DIA}",
        data={"militar_id": roana.id}, follow_redirects=False)
    destino = r.headers["location"]
    assert f"militar_id={silva.id}" in destino and f"de={DIA}" in destino


# --- deixar a vaga vazia ----------------------------------------------------

def test_descobrir_apaga_o_servico_e_audita(logado, db, cenario):
    _, s, silva, _ = cenario
    _impede(db, silva)
    db.commit()
    r = logado.post(f"/gestao/conflitos/servico/{s.id}/descobrir", follow_redirects=False)
    assert r.status_code == 303
    assert "ok=vaga-descoberta" in r.headers["location"]
    assert db.get(Servico, s.id) is None
    reg = db.scalar(select(Auditoria).where(Auditoria.acao == "descobrir"))
    assert reg is not None and reg.dados_antes["militar_id"] == silva.id


# --- permuta no caminho -----------------------------------------------------

def test_servico_com_permuta_nao_oferece_troca(logado, db, cenario):
    """Quem cobre está cobrindo por ESTA pessoa (regra 9): trocar o escalado
    tornaria a permuta mentirosa. A tela manda para a tela de permutas."""
    _, s, silva, roana = cenario
    _impede(db, silva)
    db.add(Permuta(servico_id=s.id, militar_substituto_id=roana.id))
    db.commit()
    r = logado.get("/gestao/conflitos")
    assert "permuta registrada" in r.text
    assert f"/gestao/permutas/servico/{s.id}" in r.text


def test_troca_de_servico_permutado_e_recusada(logado, db, cenario):
    _, s, silva, roana = cenario
    _impede(db, silva)
    db.add(Permuta(servico_id=s.id, militar_substituto_id=roana.id))
    db.commit()
    r = logado.post(f"/gestao/conflitos/servico/{s.id}/substituir",
                    data={"militar_id": roana.id})
    assert r.status_code == 400
    assert "permuta" in r.text


# --- tela de escolher outro -------------------------------------------------

def test_tela_de_escolha_lista_os_participantes(logado, db, cenario):
    _, s, silva, roana = cenario
    _impede(db, silva)
    db.commit()
    r = logado.get(f"/gestao/conflitos/servico/{s.id}")
    assert r.status_code == 200
    assert "ROANA" in r.text
    assert f'value="{silva.id}"' not in r.text     # o escalado não é candidato a si mesmo


def test_tela_de_escolha_de_servico_inexistente_volta_para_a_lista(logado):
    r = logado.get("/gestao/conflitos/servico/999999", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/gestao/conflitos"


# --- o desvio que fecha a demanda -------------------------------------------

def test_impedimento_sobre_dia_ja_escalado_leva_aos_conflitos(logado, db, cenario):
    """O defeito relatado: gravava o impedimento, dizia 'ok' e o serviço ficava.

    O gestor cai na tela de resolução, com o período já filtrado. Desde 01/08
    (item 2) isto vale para o que o reajuste automático NÃO pode consertar: o
    dia corrente e o passado — o serviço de hoje já começou, e reescrever o
    passado não é ajuste. Para o futuro, ver o teste seguinte.

    ⚠️ A data sai de `date.today()`: cravada, o teste passaria a exercitar o
    caminho oposto assim que a suíte rodasse depois daquele dia.
    """
    escala, _, silva, _ = cenario
    hoje = date.today()
    _servico(db, escala, silva, hoje)
    db.commit()
    r = logado.post("/gestao/impedimentos", data={
        "militar_id": silva.id, "tipo_impedimento_id": 1,
        "inicio": hoje.isoformat(), "fim": hoje.isoformat(), "observacao": "dispensa",
    }, follow_redirects=False)
    assert r.status_code == 303
    destino = r.headers["location"]
    assert destino.startswith("/gestao/conflitos")
    assert f"militar_id={silva.id}" in destino
    assert "ok=impedimento-com-conflito" in destino


def test_impedimento_sobre_dia_futuro_e_resolvido_pelo_reajuste(logado, db, cenario):
    """Item 2 (01/08): o dia futuro deixou de virar conflito.

    A escala se reajusta do dia em diante sozinha, sem o brigada "rodar" nada, e
    ele vai para o relatório do que mudou em vez da fila de conflitos — que
    estaria vazia.
    """
    escala, _, silva, roana = cenario
    futuro = date.today() + timedelta(days=4)
    _servico(db, escala, silva, futuro)
    db.commit()
    r = logado.post("/gestao/impedimentos", data={
        "militar_id": silva.id, "tipo_impedimento_id": 1,
        "inicio": futuro.isoformat(), "fim": futuro.isoformat(),
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/gestao/reajuste/")

    # e o dia realmente trocou de escalado, sem ninguém pedir
    s = db.scalar(select(Servico).where(Servico.dia == futuro))
    assert s is not None and s.militar_id == roana.id


def test_impedimento_sem_dia_escalado_volta_para_a_lista_de_sempre(logado, db, cenario):
    _, _, silva, _ = cenario
    futuro = DIA + timedelta(days=60)
    r = logado.post("/gestao/impedimentos", data={
        "militar_id": silva.id, "tipo_impedimento_id": 1,
        "inicio": futuro.isoformat(), "fim": futuro.isoformat(),
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/gestao/impedimentos")
    assert "ok=impedimento-criado" in r.headers["location"]


def test_toda_chave_ok_usada_aqui_tem_traducao(logado, db, cenario):
    """Mesma guarda das outras telas: chave sem tradução não exibe nada."""
    from app.web import AVISOS
    for chave in ("impedimento-com-conflito", "conflito-resolvido", "vaga-descoberta"):
        assert chave in AVISOS
