"""Item 2 do bloco de 01/08: a escala se reajusta sozinha do dia em diante.

Pedido do Brigada: *"sempre que houver alteração na escala o sistema deverá
disparar o processo de ajustar a escala, sem a necessidade de 'rodar' a escala
novamente — do dia em questão para a frente"*.

⚠️ **As datas saem de `date.today()`**, nunca cravadas: o reajuste não toca em
nada antes de amanhã, então uma data fixa faria o teste exercitar o caminho
oposto conforme o dia em que a suíte roda. Mesma armadilha anotada em
`tests/test_lancamento.py`.
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
from app.services import reajuste

HOJE = date.today()
AMANHA = HOJE + timedelta(days=1)


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


def _escala(db, nome="Oficial de Dia", postos=1, folga=24):
    e = Escala(nome=nome, inicio_servico=time(8, 0), duracao_horas=24,
               folga_minima_horas=folga)
    e.postos = [Posto(ordem=i + 1, rotulo=f"{i + 1}º") for i in range(postos)]
    db.add(e)
    db.flush()
    return e


def _servico(db, escala, militar, dia, posto=None):
    s = Servico(escala_id=escala.id, posto_id=(posto or escala.postos[0]).id,
                militar_id=militar.id, dia=dia,
                cor=Cor.VERMELHA if dia.weekday() >= 5 else Cor.PRETA,
                inicio_dt=datetime.combine(dia, time(8, 0)),
                termino_dt=datetime.combine(dia, time(8, 0)) + timedelta(hours=24))
    db.add(s)
    db.flush()
    return s


@pytest.fixture()
def mes(db):
    """Escala de 1 posto com 3 participantes e 6 dias fechados a partir de hoje.

    Folga de 24h (o piso) para que a rotação caiba em dias consecutivos.
    """
    e = _escala(db)
    ms = [_militar(db, n) for n in ("ALFA", "BRAVO", "CHARLIE")]
    for m in ms:
        db.add(Participacao(militar_id=m.id, escala_id=e.id))
    for i in range(6):
        _servico(db, e, ms[i % 3], HOJE + timedelta(days=i))
    db.commit()
    return e, ms


def _quem(db, escala, dia):
    s = db.scalar(select(Servico).where(Servico.escala_id == escala.id, Servico.dia == dia))
    return None if s is None else db.get(Militar, s.militar_id).nome_guerra


# --- o núcleo: do dia em diante, e só ---------------------------------------

def test_nao_toca_em_hoje_nem_no_passado(db, mes):
    """O serviço de hoje já começou; o de ontem acabou. Reescrever isso não é
    ajuste, é falsificação."""
    e, ms = mes
    antes_hoje = _quem(db, e, HOJE)
    reajuste.reajustar(db, e.id, HOJE - timedelta(days=30))
    db.flush()
    assert _quem(db, e, HOJE) == antes_hoje


def test_reajusta_de_um_dia_em_diante_deixando_os_anteriores(db, mes):
    """O exemplo do Brigada: dispensa no dia 15, só do 15 em diante muda."""
    e, ms = mes
    corte = HOJE + timedelta(days=3)
    antes = {d: _quem(db, e, HOJE + timedelta(days=d)) for d in range(6)}

    # ALFA impedido do corte em diante
    db.add(Impedimento(militar_id=ms[0].id, tipo_impedimento_id=1,
                       inicio=corte, fim=corte + timedelta(days=10)))
    db.flush()
    r = reajuste.reajustar(db, e.id, corte)
    db.flush()

    for d in range(3):          # antes do corte, nada mudou
        assert _quem(db, e, HOJE + timedelta(days=d)) == antes[d]
    for d in range(3, 6):       # do corte em diante, ALFA não serve mais
        assert _quem(db, e, HOJE + timedelta(days=d)) != "ALFA"
    assert r.mudou


def test_nao_estende_a_escala_alem_do_que_estava_fechado(db, mes):
    """Reajustar não é fechar mês novo — isso continua sendo ato do gestor."""
    e, _ = mes
    ultimo = HOJE + timedelta(days=5)
    reajuste.reajustar(db, e.id, AMANHA)
    db.flush()
    assert db.scalar(select(Servico).where(Servico.dia == ultimo + timedelta(days=1))) is None


def test_sem_nada_fechado_a_frente_nao_faz_nada(db):
    e = _escala(db)
    m = _militar(db, "ALFA")
    db.add(Participacao(militar_id=m.id, escala_id=e.id))
    _servico(db, e, m, HOJE - timedelta(days=5))
    db.commit()
    r = reajuste.reajustar(db, e.id, HOJE - timedelta(days=5))
    assert not r.mudou and r.dias_processados == 0


# --- permuta: o que o sistema NÃO desfaz sozinho -----------------------------

def test_dia_com_permuta_nao_e_refeito(db, mes):
    """Decisão do usuário (01/08): a permuta é acerto entre duas pessoas,
    autorizado pelo gestor. O reajuste automático não a desmancha."""
    e, ms = mes
    dia = HOJE + timedelta(days=2)
    s = db.scalar(select(Servico).where(Servico.escala_id == e.id, Servico.dia == dia))
    escalado_antes = s.militar_id
    db.add(Permuta(servico_id=s.id, militar_substituto_id=ms[2].id))
    db.flush()

    db.add(Impedimento(militar_id=escalado_antes, tipo_impedimento_id=1,
                       inicio=AMANHA, fim=AMANHA + timedelta(days=10)))
    db.flush()
    r = reajuste.reajustar(db, e.id, AMANHA)
    db.flush()

    depois = db.scalar(select(Servico).where(Servico.escala_id == e.id, Servico.dia == dia))
    assert depois.militar_id == escalado_antes         # o dia ficou como estava
    assert db.scalar(select(Permuta).where(Permuta.servico_id == depois.id)) is not None
    assert dia in r.pulados_por_permuta
    assert all(d.dia != dia for d in r.dias_alterados)


# --- o que o gestor vê -------------------------------------------------------

def test_o_antes_e_o_depois_ficam_guardados(db, mes):
    """Depois de gravar, o "antes" não existe mais em lugar nenhum. Ou vai para
    a auditoria, ou o gestor nunca fica sabendo o que o sistema fez."""
    e, ms = mes
    db.add(Impedimento(militar_id=ms[0].id, tipo_impedimento_id=1,
                       inicio=AMANHA, fim=AMANHA + timedelta(days=10)))
    db.flush()
    rid = reajuste.registrar_auditoria(
        db, gestor_id=1, origem="impedimento-criado",
        reajustes=[reajuste.reajustar(db, e.id, AMANHA)])
    db.commit()

    reg = db.get(Auditoria, rid)
    assert reg.entidade == "reajuste" and reg.acao == "reajustar"
    alterados = reg.dados_depois["reajustes"][0]["alterados"]
    assert alterados and alterados[0]["antes"] and alterados[0]["depois"]
    assert alterados[0]["antes"] != alterados[0]["depois"]


def test_nada_mudou_nao_gera_registro(db, mes):
    """Reajuste que não muda ninguém não vira tela nem linha de histórico.

    Roda duas vezes: a primeira alinha o mês ao que o motor produz (a fixture
    monta o rodízio à mão), a segunda não tem o que mudar. De quebra, é a prova
    de que reajustar é idempotente — sem isso, cada dispensa embaralharia a
    escala mesmo sem motivo.
    """
    e, _ = mes
    reajuste.reajustar(db, e.id, AMANHA)
    db.flush()
    rid = reajuste.registrar_auditoria(
        db, gestor_id=1, origem="impedimento-criado",
        reajustes=[reajuste.reajustar(db, e.id, AMANHA)])
    assert rid is None


def test_a_tela_reconstitui_o_reajuste(logado, db, mes):
    e, ms = mes
    db.add(Impedimento(militar_id=ms[0].id, tipo_impedimento_id=1,
                       inicio=AMANHA, fim=AMANHA + timedelta(days=10)))
    db.flush()
    rid = reajuste.registrar_auditoria(
        db, gestor_id=1, origem="impedimento-criado",
        reajustes=[reajuste.reajustar(db, e.id, AMANHA)])
    db.commit()

    r = logado.get(f"/gestao/reajuste/{rid}")
    assert r.status_code == 200
    assert "A escala foi reajustada" in r.text
    assert "ALFA" in r.text                    # aparece no "antes"


def test_reajuste_inexistente_nao_estoura(logado):
    r = logado.get("/gestao/reajuste/9999", follow_redirects=False)
    assert r.status_code == 303
    assert "falha=reajuste-inexistente" in r.headers["location"]


def test_tela_do_reajuste_exige_login(client):
    r = client.get("/gestao/reajuste/1", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/gestao/login" in r.headers["location"]


# --- a janela do boletim -----------------------------------------------------

def test_quinta_publica_o_bloco_ate_segunda(db):
    """Prática da OM dita pelo Brigada: na quinta sai sexta, sábado, domingo e
    segunda; nos outros dias, só o dia seguinte."""
    quinta = date(2026, 8, 6)
    assert reajuste.dias_no_boletim(quinta) == {
        date(2026, 8, 7), date(2026, 8, 8), date(2026, 8, 9), date(2026, 8, 10)}
    segunda = date(2026, 8, 3)
    assert reajuste.dias_no_boletim(segunda) == {date(2026, 8, 4)}


def test_dia_alterado_dentro_do_boletim_e_marcado(db, mes):
    """O gestor precisa saber que aquele dia já saiu publicado: o sistema não
    corrige o documento, ele pede aditamento."""
    e, ms = mes
    # força "hoje" numa segunda-feira, para a janela ser só o dia seguinte
    segunda = HOJE - timedelta(days=HOJE.weekday())
    dia_alvo = segunda + timedelta(days=1)
    e2 = _escala(db, "Guarda")
    a, b = _militar(db, "DELTA"), _militar(db, "ECHO")
    db.add_all([Participacao(militar_id=a.id, escala_id=e2.id),
                Participacao(militar_id=b.id, escala_id=e2.id)])
    _servico(db, e2, a, dia_alvo)
    db.add(Impedimento(militar_id=a.id, tipo_impedimento_id=1,
                       inicio=dia_alvo, fim=dia_alvo))
    db.commit()

    r = reajuste.reajustar(db, e2.id, dia_alvo, hoje=segunda)
    assert r.dias_alterados and r.dias_alterados[0].no_boletim


# --- gatilhos ----------------------------------------------------------------

def test_permuta_nao_dispara_reajuste(logado, db, mes):
    """Regra 9 + decisão do item 1: a permuta não mexe na fila. Se disparasse,
    os dias seguintes mudariam sem que nada tivesse mudado."""
    e, ms = mes
    dia = HOJE + timedelta(days=2)      # CHARLIE, pelo rodízio da fixture
    s = db.scalar(select(Servico).where(Servico.escala_id == e.id, Servico.dia == dia))
    antes = {d: _quem(db, e, HOJE + timedelta(days=d)) for d in range(6)}

    r = logado.post(f"/gestao/permutas/servico/{s.id}",
                    data={"militar_substituto_id": str(ms[0].id)},   # ALFA cobre
                    follow_redirects=False)
    assert r.status_code == 303
    assert "/gestao/reajuste/" not in r.headers["location"]
    for d in range(6):
        assert _quem(db, e, HOJE + timedelta(days=d)) == antes[d]


def test_servico_lancado_a_mao_dispara_reajuste(logado, db, mes):
    """Serviço lançado muda a folga e a fila (6.2/7.4) — logo, quem entra depois."""
    e, ms = mes
    r = logado.post("/gestao/servicos", data={
        "escala_id": str(e.id), "posto_id": str(e.postos[0].id),
        "militar_id": str(ms[1].id), "dia": (HOJE - timedelta(days=10)).isoformat(),
        "ano": str(HOJE.year), "mes": str(HOJE.month), "confirmado": "1",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/gestao/reajuste/")


def test_militar_desativado_sai_da_fila(logado, db, mes):
    """⚠️ Defeito achado em 01/08 ao ligar este gatilho: o militar desativado
    CONTINUAVA sendo escalado, enquanto a tela dizia que ele "saiu da rotação"."""
    e, ms = mes
    r = logado.post(f"/gestao/militares/{ms[0].id}/desativar", follow_redirects=False)
    assert r.status_code == 303
    for d in range(1, 6):
        assert _quem(db, e, HOJE + timedelta(days=d)) != "ALFA"
