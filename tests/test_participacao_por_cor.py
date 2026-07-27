"""Participação restrita a uma cor — regra 3.3.1.

O caso real: o militar cuja função o impede de servir em dia útil participa da
escala, mas só é escalado em fim de semana e feriado. Não se confunde com a cor
da ESCALA (regra 4.2/4.5, o Museu): ali a escala não roda na preta; aqui ela
roda, e é a PESSOA que concorre em uma cor só.

Cobre o domínio (fila), a leitura do banco, a rotação, a tela da escala, o
painel e a importação do histórico.
"""
from datetime import date, datetime, time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.domain.models import Cor, Escala as EscalaDom, Militar as MilitarDom
from app.domain.models import Participacao as ParticipacaoDom
from app.domain.motor import fila_ordenada, proximos
from app.main import app
from app.models.escala import Escala, Participacao, Posto
from app.models.gestao import Auditoria
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.models.servico import Servico
from app.seeds import seed_circulos, seed_postos_graduacao
from app.seeds.usuario import criar_ou_atualizar_gestor
from app.services import importacao_csv, mapeamento, painel, rotacao

SEXTA = date(2026, 7, 3)      # dia útil -> preta
SABADO = date(2026, 7, 4)     # -> vermelha


# --- domínio: quem não concorre na cor não entra na fila ---------------------
def _militar(i: int) -> MilitarDom:
    return MilitarDom(id=i, nome_guerra=f"M{i}", nome_completo=f"Militar {i}",
                      posto="2º Sgt", om="QG", numero_antiguidade=100 - i)


def test_fila_da_preta_ignora_quem_so_concorre_na_vermelha():
    """Regra 3.3.1 — fora da fila, não 'pulado'."""
    dois = ParticipacaoDom(militar=_militar(2), escala_id=1, serve_preta=False)
    fila = fila_ordenada([ParticipacaoDom(militar=_militar(1), escala_id=1), dois], Cor.PRETA)
    assert [p.militar.id for p in fila] == [1]


def test_o_mesmo_militar_esta_na_fila_da_vermelha():
    dois = ParticipacaoDom(militar=_militar(2), escala_id=1, serve_preta=False)
    fila = fila_ordenada([ParticipacaoDom(militar=_militar(1), escala_id=1), dois], Cor.VERMELHA)
    assert {p.militar.id for p in fila} == {1, 2}


def test_restricao_nao_guarda_a_vez_como_o_impedimento_guarda():
    """Regra 6.4 vale para impedimento; a restrição de cor é outra coisa.

    Quem só concorre na vermelha e serviu no sábado NÃO empurra ninguém na fila
    da preta: ele simplesmente não está lá, sirva ou não sirva.
    """
    restrito = ParticipacaoDom(militar=_militar(1), escala_id=1, serve_preta=False,
                               ultima_vermelha=SABADO)
    outro = ParticipacaoDom(militar=_militar(2), escala_id=1, ultima_preta=SEXTA)
    escala = EscalaDom(id=1, nome="Oficial de Dia", postos=1)
    escolhidos = proximos(escala, [restrito, outro], Cor.PRETA,
                          date(2026, 7, 6), [], {})
    assert [p.militar.id for p in escolhidos] == [2]


def test_participante_sem_cor_nenhuma_e_recusado_pelo_banco():
    """CHECK ck_participacao_cor: participar sem concorrer seria não participar."""
    from sqlalchemy.exc import IntegrityError
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.execute(__import__("sqlalchemy").text("PRAGMA foreign_keys=OFF"))
        s.add(Participacao(militar_id=1, escala_id=1,
                           serve_preta=False, serve_vermelha=False))
        with pytest.raises(IntegrityError):
            s.commit()


# --- banco + rotação ---------------------------------------------------------
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
    s.add(OrganizacaoMilitar(id=1, nome="Quartel General", sigla="QG", propria=True))
    criar_ou_atualizar_gestor(s, "brigada", "senha-boa-123", "Sgt Brigada")
    s.flush()
    sgt = s.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "2º Sgt"))
    for i in (1, 2):
        s.add(Militar(id=i, nome_guerra=f"M{i}", nome_completo=f"Militar {i}",
                      posto_graduacao_id=sgt, om_id=1, numero_antiguidade=100 - i))
    s.add(Escala(id=1, nome="Oficial de Dia", folga_minima_horas=48))
    s.flush()
    s.add(Posto(id=1, escala_id=1, ordem=1))
    s.add(Participacao(militar_id=1, escala_id=1))                      # as duas cores
    s.add(Participacao(militar_id=2, escala_id=1, serve_preta=False))   # só vermelha
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


def test_a_leitura_do_banco_leva_as_cores_para_o_dominio(db):
    """Sem isto o motor não teria como aplicar a regra: o dado ficaria no banco."""
    parts = {p.militar.id: p for p in mapeamento.participacoes_da_escala(db, 1, SEXTA)}
    assert parts[1].serve_preta and parts[1].serve_vermelha
    assert parts[2].serve_preta is False and parts[2].serve_vermelha is True


def test_dia_util_nao_escala_quem_so_serve_na_vermelha(db):
    r = rotacao.escalar_dia(db, 1, SEXTA)
    assert r.cor is Cor.PRETA
    assert [m.id for m in r.escolhidos] == [1]


def _vermelha_anterior_de_m1(db):
    """M1 serve o sábado anterior — assim o mais folgado da vermelha é o M2."""
    rotacao.gravar_dia(db, rotacao.escalar_dia(db, 1, date(2026, 6, 27)))
    db.commit()


def test_sabado_escala_o_restrito_normalmente(db):
    """Na vermelha o restrito concorre como qualquer um: é o mais folgado."""
    _vermelha_anterior_de_m1(db)
    r = rotacao.escalar_dia(db, 1, SABADO)
    assert r.cor is Cor.VERMELHA
    assert [m.id for m in r.escolhidos] == [2]


def test_a_folga_minima_continua_valendo_entre_cores(db):
    """Regra 7.4 não muda: serviu sábado, não assume no domingo.

    A restrição de cor tira o militar de UMA fila; não o torna disponível a
    qualquer momento na fila que sobrou.
    """
    _vermelha_anterior_de_m1(db)
    rotacao.gravar_dia(db, rotacao.escalar_dia(db, 1, SABADO))    # M2 serve
    db.commit()
    domingo = rotacao.escalar_dia(db, 1, date(2026, 7, 5))
    assert [m.id for m in domingo.escolhidos] == [1]     # M2 ainda em folga


def test_escala_sem_ninguem_na_preta_avisa_efetivo_insuficiente(db):
    """Regra 7.8 — a escala roda na preta, mas ninguém concorre nela."""
    vinculo = db.scalar(select(Participacao).where(Participacao.militar_id == 1))
    vinculo.serve_preta = False
    db.commit()
    r = rotacao.escalar_dia(db, 1, SEXTA)
    assert r.escolhidos == [] and r.efetivo_insuficiente


# --- painel ------------------------------------------------------------------
def test_painel_acusa_o_buraco_da_cor_e_diz_qual(db):
    vinculo = db.scalar(select(Participacao).where(Participacao.militar_id == 1))
    vinculo.serve_preta = False          # ninguém concorre na preta
    db.commit()
    curtas = painel.alertas(db, SEXTA).mal_configuradas
    assert len(curtas) == 1
    assert curtas[0]["cor"] == "preta" and curtas[0]["participantes"] == 0


def test_faltando_o_mesmo_nas_duas_cores_o_aviso_e_um_so(db):
    """Falta de gente na escala não é restrição de cor — não repetir o aviso."""
    db.add(Posto(escala_id=1, ordem=2))
    db.add(Posto(escala_id=1, ordem=3))   # 3 vagas para 2 participantes
    vinculo = db.scalar(select(Participacao).where(Participacao.militar_id == 2))
    vinculo.serve_preta = True            # os dois nas duas cores
    db.commit()
    curtas = painel.alertas(db, SEXTA).mal_configuradas
    assert len(curtas) == 1 and curtas[0]["cor"] == ""


def test_a_fila_da_escala_mostra_a_restricao(db):
    """Barra curta sem explicação parece injustiça na leitura de equidade."""
    lugares = {l.militar.id: l.restricao
               for l in painel.fila_por_servicos(db, 1, date(2026, 1, 1), date(2026, 12, 31))}
    assert lugares[1] == "" and lugares[2] == "só vermelha"


# --- tela da escala (regra 11: gestão com login) ------------------------------
def test_tela_da_escala_oferece_a_escolha_de_cor(client):
    html = client.get("/gestao/escalas/1").text
    assert "Concorre em" in html and "só vermelha" in html


def test_escala_de_uma_cor_so_nao_pergunta_a_cor_do_participante(client, db):
    """No Museu (só vermelha) a pergunta não teria resposta possível."""
    db.add(Escala(id=2, nome="Museu", tem_preta=False))
    db.flush()
    db.add(Posto(escala_id=2, ordem=1))
    db.add(Participacao(militar_id=1, escala_id=2))
    db.commit()
    assert "Concorre em" not in client.get("/gestao/escalas/2").text


def test_gestor_restringe_a_cor_e_fica_auditado(client, db):
    r = client.post("/gestao/escalas/1/participantes/1/cores",
                    data={"cores": "vermelha"}, follow_redirects=False)
    assert r.status_code == 303 and "ok=participante-cores" in r.headers["location"]
    db.expire_all()
    vinculo = db.scalar(select(Participacao).where(Participacao.militar_id == 1))
    assert vinculo.serve_preta is False and vinculo.serve_vermelha is True
    registro = db.scalars(select(Auditoria).where(Auditoria.entidade == "participacao")).all()
    assert registro and registro[-1].acao == "alterar"


def test_valor_desconhecido_cai_em_ambas_nunca_em_nenhuma(client, db):
    """A URL não pode produzir participante que não concorre em cor alguma."""
    client.post("/gestao/escalas/1/participantes/2/cores", data={"cores": "roxa"},
                follow_redirects=False)
    db.expire_all()
    vinculo = db.scalar(select(Participacao).where(Participacao.militar_id == 2))
    assert vinculo.serve_preta and vinculo.serve_vermelha


def test_incluir_participante_ja_restrito(client, db):
    db.add(Militar(id=3, nome_guerra="M3", nome_completo="Militar 3",
                   posto_graduacao_id=db.scalar(select(PostoGraduacao.id)
                                                .where(PostoGraduacao.sigla == "2º Sgt")),
                   om_id=1, numero_antiguidade=70))
    db.commit()
    client.post("/gestao/escalas/1/participantes",
                data={"militar_id": "3", "cores": "vermelha"}, follow_redirects=False)
    db.expire_all()
    vinculo = db.scalar(select(Participacao).where(Participacao.militar_id == 3))
    assert vinculo.serve_preta is False and vinculo.serve_vermelha is True


# --- importação do histórico (regra 6/7) -------------------------------------
def test_csv_avisa_cor_que_o_militar_hoje_nao_concorre(db):
    """Fato consumado: grava, mas avisa — costuma ser arquivo ou cadastro errado."""
    conteudo = f"escala,data,militar\nOficial de Dia,{SEXTA.strftime('%d/%m/%Y')},M2\n"
    leitura = importacao_csv.ler(db, conteudo.encode("utf-8"))
    assert leitura.aceitas and "não concorre na preta" in leitura.aceitas[0].avisos[0]
    assert importacao_csv.aplicar(db, leitura) == 1     # continua gravando


def test_csv_nao_avisa_quando_a_cor_confere(db):
    conteudo = f"escala,data,militar\nOficial de Dia,{SABADO.strftime('%d/%m/%Y')},M2\n"
    leitura = importacao_csv.ler(db, conteudo.encode("utf-8"))
    assert leitura.aceitas[0].avisos == []


# --- consulta aberta: cortina de escalas (regra 13.1) -------------------------
def test_consulta_traz_a_cortina_e_diz_qual_escala_esta_na_tela(db):
    """Com a lista recolhida, o resumo é quem informa a escala exibida."""
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        html = c.get("/").text
    app.dependency_overrides.clear()
    assert "<details class=\"menu-escalas\">" in html
    assert "<summary>" in html and "Oficial de Dia" in html
