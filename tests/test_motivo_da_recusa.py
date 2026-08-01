"""Item 4 do bloco de 01/08: toda restrição diz o motivo.

O pedido do Brigada veio pelo exemplo da permuta ("o sistema restringe mas diz o
motivo"), e ali já dizia. A restrição que continuava MUDA era outra, e é a que
mais custa: o dia que fecha com menos militares que postos (regra 7.8). A tela
de Escalar dava um número — "dias com efetivo insuficiente: 3" — e nada mais.
Faltar gente, estarem todos de férias e a folga não ter fechado pedem três
providências diferentes do gestor, e ele não tinha como distinguir.

Cobre também o outro tipo de recusa sem palavra: cair de volta na lista por
redirecionamento, sem nada escrito.
"""
from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.domain import motor
from app.domain.models import Cor
from app.main import app
from app.models.escala import Escala, Participacao, Posto
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao, TipoImpedimento
from app.models.servico import Servico
from app.seeds import seed_circulos, seed_postos_graduacao
from app.seeds.usuario import criar_ou_atualizar_gestor
from app.services import rotacao

SEG = date(2026, 8, 3)      # segunda-feira -> preta


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
    s.add(TipoImpedimento(id=1, nome="Férias"))
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


def _escala(db, nome="Oficial de Dia", postos=1, folga=48):
    e = Escala(nome=nome, inicio_servico=time(8, 0), duracao_horas=24,
               folga_minima_horas=folga)
    e.postos = [Posto(ordem=i + 1, rotulo=f"{i + 1}º") for i in range(postos)]
    db.add(e)
    db.flush()
    return e


def _servico(db, escala, militar, dia):
    s = Servico(escala_id=escala.id, posto_id=escala.postos[0].id, militar_id=militar.id,
                dia=dia, cor=Cor.VERMELHA if dia.weekday() >= 5 else Cor.PRETA,
                inicio_dt=datetime.combine(dia, time(8, 0)),
                termino_dt=datetime.combine(dia, time(8, 0)) + timedelta(hours=24))
    db.add(s)
    db.flush()
    return s


# --- o motor guarda o motivo (domínio puro) ----------------------------------

def test_motivo_folga_traz_a_data_em_que_o_militar_libera(db):
    """"Sem folga" sozinho não é acionável; com a data, é."""
    e = _escala(db, folga=48)
    silva = _militar(db, "SILVA")
    db.add(Participacao(militar_id=silva.id, escala_id=e.id))
    _servico(db, e, silva, SEG - timedelta(days=1))    # saiu domingo 08:00 (+24h)
    db.commit()

    r = rotacao.escalar_dia(db, e.id, SEG)
    assert r.efetivo_insuficiente
    assert [p.motivo for p in r.preteridos] == [motor.MOTIVO_FOLGA]
    # término domingo 08:00 + 24h = segunda 08:00; + 48h de folga = quarta 08:00
    assert r.preteridos[0].livre_em == datetime(2026, 8, 5, 8, 0)


def test_motivo_impedido_e_distinto_do_motivo_folga(db):
    e = _escala(db)
    silva = _militar(db, "SILVA")
    db.add(Participacao(militar_id=silva.id, escala_id=e.id))
    db.add(Impedimento(militar_id=silva.id, tipo_impedimento_id=1,
                       inicio=SEG, fim=SEG + timedelta(days=10)))
    db.commit()

    r = rotacao.escalar_dia(db, e.id, SEG)
    assert [p.motivo for p in r.preteridos] == [motor.MOTIVO_IMPEDIDO]
    assert r.preteridos[0].livre_em is None      # não é caso de esperar folga


def test_motivo_cor_e_motivo_inativo(db):
    """Os dois que nunca chegam à fila (3.3.1 e 7.6) também precisam aparecer:
    são metade da resposta a 'por que faltou gente'."""
    e = _escala(db)
    so_vermelha, isento = _militar(db, "VERM"), _militar(db, "ISENTO")
    db.add(Participacao(militar_id=so_vermelha.id, escala_id=e.id, serve_preta=False))
    db.add(Participacao(militar_id=isento.id, escala_id=e.id, ativo=False))
    db.commit()

    r = rotacao.escalar_dia(db, e.id, SEG)          # segunda = preta
    motivos = sorted(p.motivo for p in r.preteridos)
    assert motivos == [motor.MOTIVO_COR, motor.MOTIVO_INATIVO]


def test_dia_que_fecha_nao_acusa_quem_estava_abaixo_da_linha(db):
    """Quem não chegou a ser examinado não foi 'recusado' — não vira preterido."""
    e = _escala(db, postos=1)
    a, b = _militar(db, "ALFA"), _militar(db, "BRAVO")
    db.add_all([Participacao(militar_id=a.id, escala_id=e.id),
                Participacao(militar_id=b.id, escala_id=e.id)])
    db.commit()

    r = rotacao.escalar_dia(db, e.id, SEG)
    assert not r.efetivo_insuficiente
    assert r.preteridos == []


def test_proximos_continua_devolvendo_so_os_escolhidos(db):
    """`proximos` virou açúcar sobre `selecionar`: a assinatura antiga vale."""
    e = _escala(db, postos=2)
    a, b, c = (_militar(db, "ALFA"), _militar(db, "BRAVO"), _militar(db, "CHARLIE"))
    for m in (a, b, c):
        db.add(Participacao(militar_id=m.id, escala_id=e.id))
    db.commit()

    r = rotacao.escalar_dia(db, e.id, SEG)
    assert len(r.escolhidos) == 2


# --- a tela de Escalar diz o porquê ------------------------------------------

def test_escalar_mostra_o_motivo_de_cada_dia_curto(logado, db):
    e = _escala(db, postos=2)
    silva, costa = _militar(db, "SILVA"), _militar(db, "COSTA")
    db.add_all([Participacao(militar_id=silva.id, escala_id=e.id),
                Participacao(militar_id=costa.id, escala_id=e.id)])
    db.add(Impedimento(militar_id=costa.id, tipo_impedimento_id=1,
                       inicio=SEG, fim=SEG))
    db.commit()

    r = logado.post("/gestao/escalar", data={
        "escala_id": str(e.id), "inicio": SEG.isoformat(), "fim": SEG.isoformat()})
    assert r.status_code == 200
    assert "Por que faltou gente" in r.text
    assert "com impedimento no dia" in r.text and "regra 7.5" in r.text
    assert "1 de 2 vaga" in r.text


def test_escalar_diz_quem_sai_da_folga_primeiro(logado, db):
    """A linha que responde 'então o que eu faço?'."""
    e = _escala(db, postos=1, folga=48)
    silva = _militar(db, "SILVA")
    db.add(Participacao(militar_id=silva.id, escala_id=e.id))
    _servico(db, e, silva, SEG - timedelta(days=1))
    db.commit()

    r = logado.post("/gestao/escalar", data={
        "escala_id": str(e.id), "inicio": SEG.isoformat(), "fim": SEG.isoformat()})
    assert "Sai da folga primeiro" in r.text
    assert "SILVA" in r.text and "05/08" in r.text


def test_periodo_sem_falta_nao_mostra_o_bloco(logado, db):
    """Sem dia curto, nada de seção — aviso permanente vira ruído."""
    e = _escala(db, postos=1)
    a, b, c = (_militar(db, "ALFA"), _militar(db, "BRAVO"), _militar(db, "CHARLIE"))
    for m in (a, b, c):
        db.add(Participacao(militar_id=m.id, escala_id=e.id))
    db.commit()

    r = logado.post("/gestao/escalar", data={
        "escala_id": str(e.id), "inicio": SEG.isoformat(),
        "fim": (SEG + timedelta(days=2)).isoformat()})
    assert "Por que faltou gente" not in r.text


def test_muitos_dias_curtos_sao_cortados_e_a_tela_avisa(db):
    """Um ano curto renderizaria 365 blocos. Corta e diz quantos ficaram."""
    e = _escala(db, postos=1)
    resultados = [
        rotacao.ResultadoDia(escala_id=1, dia=SEG + timedelta(days=i), cor=Cor.PRETA,
                             escolhidos=[], postos_solicitados=1,
                             efetivo_insuficiente=True)
        for i in range(40)
    ]
    faltas, total = rotacao.explicar_faltas(resultados, limite=20)
    assert len(faltas) == 20 and total == 40


def test_contagem_por_motivo_em_vez_de_lista_de_nomes(db):
    """Numa escala de 139, listar nomes não é informação — contar é."""
    e = _escala(db, postos=1)
    for i in range(5):
        m = _militar(db, f"M{i}")
        db.add(Participacao(militar_id=m.id, escala_id=e.id))
        db.add(Impedimento(militar_id=m.id, tipo_impedimento_id=1, inicio=SEG, fim=SEG))
    db.commit()

    faltas, total = rotacao.explicar_faltas([rotacao.escalar_dia(db, e.id, SEG)])
    assert total == 1
    assert faltas[0].por_motivo == [
        (rotacao.ROTULO_MOTIVO[motor.MOTIVO_IMPEDIDO], 5)
    ]
    assert faltas[0].vagas_abertas == 1


# --- painel: a véspera sem candidato ----------------------------------------

def test_painel_nao_diz_mais_que_nenhuma_escala_roda(logado, db):
    """O pior tipo de recusa muda: a afirmação FALSA.

    A escala roda amanhã e está sem ninguém disponível. Antes, o painel
    descartava a escala junto com as que não rodam e escrevia "nenhuma escala
    roda amanhã" — sumindo justamente com a véspera que pede providência.
    """
    from app.services import painel

    amanha = date.today() + timedelta(days=1)
    e = _escala(db, postos=1)
    silva = _militar(db, "SILVA")
    db.add(Participacao(militar_id=silva.id, escala_id=e.id))
    db.add(Impedimento(militar_id=silva.id, tipo_impedimento_id=1,
                       inicio=amanha, fim=amanha + timedelta(days=5)))
    db.commit()

    linhas = painel.proximos_da_fila(db, amanha)
    assert len(linhas) == 1
    assert linhas[0]["militares"] == []
    assert linhas[0]["falta"].vagas_abertas == 1
    assert "impedimento no dia" in linhas[0]["falta"].por_motivo[0][0]

    r = logado.get("/gestao")
    assert "Nenhuma escala roda amanhã" not in r.text
    assert "sem candidato" in r.text


def test_escala_que_nao_roda_no_dia_continua_fora(db):
    """Regra 4.5: o Museu numa terça não está descoberto — não é falta."""
    from app.services import painel

    e = _escala(db, postos=1)
    e.tem_preta = False                       # só-vermelha
    silva = _militar(db, "SILVA")
    db.add(Participacao(militar_id=silva.id, escala_id=e.id))
    db.commit()

    assert painel.proximos_da_fila(db, SEG) == []      # segunda = preta


# --- redirecionamento que não deixava o gestor sem palavra -------------------

def test_escala_inexistente_avisa_em_vez_de_voltar_calado(logado):
    r = logado.post("/gestao/escalas/9999/postos", data={"rotulo": "X"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "falha=escala-inexistente" in r.headers["location"]
    seguinte = logado.get(r.headers["location"])
    assert "não existe mais" in seguinte.text


def test_chave_de_falha_desconhecida_nao_escreve_nada(logado):
    """A URL não pode imprimir texto na tela de gestão (mesma trava do ?ok=)."""
    r = logado.get("/gestao/escalas?falha=<b>invadido</b>")
    assert r.status_code == 200
    assert "invadido" not in r.text
