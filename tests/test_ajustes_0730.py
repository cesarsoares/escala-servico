"""Ajustes pedidos pelo usuário em 30/07 (notas.txt, bloco "demandas em 30/07/26").

Dois pedidos sobre a tela *Serviços lançados à mão*:

1. ao abrir uma escala, o `<select>` de militar deve listar **os participantes
   daquela escala**, não o efetivo inteiro — no banco real o Museu tem 11
   participantes e o campo trazia 285 nomes. Quem deve concorrer e não está lá
   se inclui em Escalas → (a escala) → Participantes, que é onde o gestor já
   administra isso;
2. a lista de escalas em chips polui a tela quando a OM tiver ~20 escalas. Virou
   a **cortina lateral** da consulta aberta, agora componente compartilhado
   (`_cortina_escalas.html`) — escolha do usuário, por consistência.

A guarda que não estava no pedido e sem a qual o item 1 vira defeito: ao
CORRIGIR uma linha, o escalado gravado tem de continuar na lista mesmo que hoje
não seja mais participante, senão abrir "corrigir" para acertar só a data
trocaria a pessoa por engano.
"""
from datetime import date, timedelta

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
from app.seeds.usuario import criar_ou_atualizar_gestor
from app.services import lancamento


def _segunda_passada() -> date:
    d = date.today() - timedelta(days=7)
    return d - timedelta(days=d.weekday())


SEG = _segunda_passada()
MES = {"ano": SEG.year, "mes": SEG.month}


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = Session(engine)
    seed_circulos(s)
    seed_postos_graduacao(s)
    s.add(OrganizacaoMilitar(id=1, nome="Quartel General", sigla="QG"))
    criar_ou_atualizar_gestor(s, "brigada", "senha-boa-123", "Sgt Brigada")
    s.commit()
    yield s
    s.close()


@pytest.fixture()
def logado(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        c.post("/gestao/login", data={"username": "brigada", "password": "senha-boa-123"},
               follow_redirects=False)
        yield c
    app.dependency_overrides.clear()


def _militar(db, nome):
    m = Militar(nome_guerra=nome, nome_completo=f"{nome} de Tal", om_id=1,
                posto_graduacao_id=db.scalar(
                    select(PostoGraduacao.id).where(PostoGraduacao.sigla == "Cap")))
    db.add(m)
    db.flush()
    return m


@pytest.fixture()
def cenario(db):
    """Uma escala com DOIS participantes e um militar de fora do quadro dela."""
    e = Escala(nome="Oficial de Dia", folga_minima_horas=48)
    e.postos = [Posto(ordem=1, rotulo="Serviço")]
    db.add(e)
    db.flush()
    silva, roana = _militar(db, "SILVA"), _militar(db, "ROANA")
    forasteiro = _militar(db, "FORASTEIRO")          # ativo, mas de outra escala
    db.add_all([Participacao(militar_id=silva.id, escala_id=e.id),
                Participacao(militar_id=roana.id, escala_id=e.id)])
    db.commit()
    return e, e.postos[0], silva, roana, forasteiro


def _opcoes_do_select(html: str, campo: str) -> str:
    """Só o trecho do `<select name="campo">`.

    Procurar o nome solto na página inteira daria falso positivo: o militar
    também aparece na tabela do mês e nas mensagens de conferência.
    """
    inicio = html.index(f'name="{campo}"')
    return html[inicio:html.index("</select>", inicio)]


# --- 1. o select traz os participantes da escala ----------------------------

def test_select_de_militar_lista_so_os_participantes(logado, cenario):
    e, _, silva, roana, forasteiro = cenario
    r = logado.get(f"/gestao/servicos?escala_id={e.id}&ano={SEG.year}&mes={SEG.month}")
    opcoes = _opcoes_do_select(r.text, "militar_id")
    assert "SILVA" in opcoes and "ROANA" in opcoes
    assert "FORASTEIRO" not in opcoes


def test_participante_isento_sai_do_select(logado, db, cenario):
    """Isentar desativa o vínculo (regra 7.6) — quem não concorre não é oferecido."""
    e, _, silva, roana, _ = cenario
    vinculo = db.scalar(select(Participacao).where(Participacao.militar_id == roana.id))
    vinculo.ativo = False
    db.commit()
    r = logado.get(f"/gestao/servicos?escala_id={e.id}&ano={SEG.year}&mes={SEG.month}")
    opcoes = _opcoes_do_select(r.text, "militar_id")
    assert "SILVA" in opcoes
    assert "ROANA" not in opcoes


def test_militar_desativado_sai_do_select(logado, db, cenario):
    e, _, silva, roana, _ = cenario
    roana.ativo = False
    db.commit()
    r = logado.get(f"/gestao/servicos?escala_id={e.id}&ano={SEG.year}&mes={SEG.month}")
    assert "ROANA" not in _opcoes_do_select(r.text, "militar_id")


def test_ao_corrigir_o_escalado_gravado_continua_na_lista(logado, db, cenario):
    """A guarda: corrigir a data não pode trocar a pessoa por engano.

    SILVA serviu e depois saiu do quadro da escala. Se o `<select>` só trouxesse
    os participantes de hoje, o gestor abriria "corrigir" para mudar a data e
    salvaria com OUTRO militar, sem perceber.
    """
    e, posto, silva, _, _ = cenario
    s = lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    vinculo = db.scalar(select(Participacao).where(Participacao.militar_id == silva.id))
    vinculo.ativo = False
    db.commit()

    r = logado.get(f"/gestao/servicos?editar={s.id}")
    opcoes = _opcoes_do_select(r.text, "militar_id")
    assert "SILVA" in opcoes
    assert f'value="{silva.id}" selected' in opcoes.replace(" >", ">")

    # e no lançamento novo (sem ?editar=) ele já não aparece
    nova = logado.get(f"/gestao/servicos?escala_id={e.id}&ano={SEG.year}&mes={SEG.month}")
    assert "SILVA" not in _opcoes_do_select(nova.text, "militar_id")


def test_escalado_gravado_e_desativado_ainda_aparece_ao_corrigir(logado, db, cenario):
    """O militar saiu da OM depois de servir: a linha continua corrigível."""
    e, posto, silva, _, _ = cenario
    s = lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    silva.ativo = False
    db.commit()
    r = logado.get(f"/gestao/servicos?editar={s.id}")
    assert "SILVA" in _opcoes_do_select(r.text, "militar_id")


def test_lancar_para_quem_nao_participa_continua_possivel_pela_api(logado, db, cenario):
    """A tela deixou de oferecer, mas o SERVIÇO segue aceitando (fato consumado).

    A restrição é de usabilidade do `<select>`; a regra não mudou. Isso importa
    para a importação de CSV e para a correção de histórico, que passam pelo
    mesmo `lancamento.analisar` e tratam "não é participante" como AVISO.
    """
    e, posto, _, _, forasteiro = cenario
    analise = lancamento.analisar(db, e.id, posto.id, SEG, forasteiro.id)
    assert analise.pode_gravar
    assert any("participante" in a for a in analise.avisos)


# --- 2. a cortina lateral no lugar dos chips --------------------------------

@pytest.mark.parametrize("caminho", ["/gestao/servicos", "/gestao/permutas"])
def test_telas_de_gestao_usam_a_cortina_de_escalas(logado, cenario, caminho):
    e = cenario[0]
    r = logado.get(f"{caminho}?escala_id={e.id}&ano={SEG.year}&mes={SEG.month}")
    assert r.status_code == 200
    assert 'id="menu-escalas"' in r.text
    # O puxador é o ÚNICO ponto de entrada: sem ele a cortina não abre.
    assert 'id="puxador-escalas"' in r.text
    assert "/static/menu.js" in r.text
    # E sem script a lista tem de voltar para a página.
    assert "<noscript>" in r.text


@pytest.mark.parametrize("caminho", ["/gestao/servicos", "/gestao/permutas"])
def test_a_tela_diz_de_qual_escala_e_o_mes(logado, cenario, caminho):
    """Com a cortina fechada, o chip marcado não está mais lá para dizer isso."""
    e = cenario[0]
    r = logado.get(f"{caminho}?escala_id={e.id}&ano={SEG.year}&mes={SEG.month}")
    assert "de-qual-escala" in r.text
    assert "Oficial de Dia" in r.text


def test_a_consulta_aberta_continua_com_a_mesma_cortina(logado, cenario):
    """O componente foi extraído da consulta: ela não podia sair perdendo nada."""
    r = logado.get("/")
    assert 'id="menu-escalas"' in r.text
    assert 'id="puxador-escalas"' in r.text
    assert "/static/menu.js" in r.text


# --- achado da conferência visual: o mês do título estava errado ------------

@pytest.mark.parametrize("mes,nome", [(1, "Janeiro"), (8, "Agosto"), (12, "Dezembro")])
def test_o_titulo_nomeia_o_mes_certo(logado, cenario, mes, nome):
    """`MESES` tem "" na posição 0 — é indexado pelo NÚMERO do mês.

    A tela usava `MESES[mes - 1]`: o título saía um mês atrasado (agosto lido
    como "julho") e em janeiro saía vazio. Achado ao conferir a captura da tela
    com os dados reais.
    """
    e = cenario[0]
    r = logado.get(f"/gestao/servicos?escala_id={e.id}&ano=2026&mes={mes}")
    assert f"{nome} / 2026" in r.text


def test_escala_extinta_aparece_marcada_na_cortina(logado, db, cenario):
    """Serviços lançados à mão alcança escala extinta — é histórico."""
    e = cenario[0]
    morta = Escala(nome="Museu", folga_minima_horas=48, ativa=False)
    morta.postos = [Posto(ordem=1)]
    db.add(morta)
    db.commit()
    r = logado.get(f"/gestao/servicos?escala_id={e.id}&ano={SEG.year}&mes={SEG.month}")
    assert "Museu" in r.text
    assert "(extinta)" in r.text
