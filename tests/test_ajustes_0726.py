"""Ajustes pedidos em 2026-07-26 — uma regressão por defeito.

  1. botão primário dentro de tabela ficava azul-escuro sobre azul-escuro;
  7. a consulta mostrava só o nome de guerra, sem posto/graduação;
  8. "ver inativos" e "ver ativos" traziam a MESMA lista;
  9. tabelas sem zebra, com hover cinza e cabeçalho apagado.
"""
from datetime import date, datetime, time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.domain.models import Cor
from app.main import app
from app.models.escala import Escala, Participacao, Posto
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.models.servico import Servico
from app.seeds import seed_circulos, seed_postos_graduacao, seed_tipos_impedimento
from app.seeds.usuario import criar_ou_atualizar_gestor

FOLHA = Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "style.css"


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
    cap = s.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "Cap"))
    sgt = s.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "2º Sgt"))
    s.add(Militar(id=1, nome_guerra="SOUZA", nome_completo="José de Souza",
                  posto_graduacao_id=cap, om_id=1))
    s.add(Militar(id=2, nome_guerra="PEREIRA", nome_completo="Ana Pereira",
                  posto_graduacao_id=sgt, om_id=1, ativo=False))
    s.add(Escala(id=1, nome="Oficial de Dia", folga_minima_horas=48))
    s.flush()
    s.add(Posto(id=1, escala_id=1, ordem=1))
    s.add(Participacao(militar_id=1, escala_id=1))
    s.add(Servico(escala_id=1, posto_id=1, militar_id=1, dia=date(2026, 7, 6),
                  inicio_dt=datetime.combine(date(2026, 7, 6), time(8, 0)),
                  termino_dt=datetime.combine(date(2026, 7, 7), time(8, 0)),
                  cor=Cor.PRETA))
    s.commit()
    yield s
    s.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _logado(client):
    client.post("/gestao/login", data={"username": "brigada", "password": "senha-boa-123"},
                follow_redirects=False)
    return client


# --- 8. o filtro do Efetivo ---------------------------------------------------
def test_lista_de_inativos_traz_so_os_inativos(client):
    """O defeito: `inativos=1` não filtrava pelos inativos, só desligava o
    filtro dos ativos — as duas telas mostravam o efetivo inteiro."""
    texto = _logado(client).get("/gestao/militares?situacao=inativos").text
    assert ">PEREIRA</a>" in texto
    assert ">SOUZA</a>" not in texto


def test_lista_de_ativos_traz_so_os_ativos(client):
    texto = _logado(client).get("/gestao/militares?situacao=ativos").text
    assert ">SOUZA</a>" in texto
    assert ">PEREIRA</a>" not in texto


def test_lista_de_todos_traz_os_dois(client):
    texto = _logado(client).get("/gestao/militares?situacao=todos").text
    assert ">SOUZA</a>" in texto and ">PEREIRA</a>" in texto


def test_situacao_invalida_cai_no_padrao_dos_ativos(client):
    """URL adulterada não vira 500 nem revela inativo sem pedir."""
    texto = _logado(client).get("/gestao/militares?situacao=xpto").text
    assert ">SOUZA</a>" in texto and ">PEREIRA</a>" not in texto


def test_inativos_1_continua_valendo_como_todos(client):
    """URL antiga guardada por alguém: `inativos=1` sempre trouxe o efetivo
    inteiro. Continua trazendo — só que agora a tela chama isso de 'todos'."""
    texto = _logado(client).get("/gestao/militares?inativos=1").text
    assert ">SOUZA</a>" in texto and ">PEREIRA</a>" in texto


def test_total_da_tela_conta_a_mesma_situacao(client):
    """Comparar '1 de 285 ativos' com uma lista de inativos mentiria."""
    assert "1 militares inativos" in _logado(client).get(
        "/gestao/militares?situacao=inativos").text


def test_reativar_volta_para_a_lista_de_inativos(client, db):
    r = _logado(client).post("/gestao/militares/2/reativar", follow_redirects=False)
    assert "situacao=inativos" in r.headers["location"]


# --- 7. posto/graduação na consulta ------------------------------------------
def test_consulta_mostra_posto_graduacao_com_o_nome(client):
    """Regra 12/3.2: militar se identifica por posto/graduação + nome."""
    texto = client.get("/?escala_id=1&ano=2026&mes=7").text
    assert '<span class="pg">Cap</span> SOUZA' in texto


def test_impressao_mostra_posto_graduacao(client):
    """Já vinha de publicacao._nome; o teste trava para não se perder."""
    assert "Cap SOUZA (QG)" in client.get("/escalas/1/impressao?ano=2026&mes=7").text


# --- 1 e 9. a folha de estilo -------------------------------------------------
def test_botao_em_tabela_nao_herda_a_cor_de_link():
    """`.tabela td a` é mais específico que `.botao` e apagava a letra branca
    do botão primário: azul-escuro sobre azul-escuro, contraste 1,3:1."""
    css = FOLHA.read_text(encoding="utf-8")
    assert ".tabela td a.botao { color:#fff; }" in css
    assert ".tabela td a.botao.secundario { color:var(--azul); }" in css


def test_acao_de_linha_das_permutas_usa_o_botao_secundario():
    """Mesmo tratamento de 'impedimentos' e 'regravar' nas outras tabelas."""
    tpl = (Path(__file__).resolve().parents[1] / "app" / "web" / "templates"
           / "gestao" / "permutas.html").read_text(encoding="utf-8")
    assert 'class="botao mini secundario"' in tpl


def test_zebra_vem_antes_do_estado_da_linha_e_do_hover():
    """A ordem importa: linha permutada caindo na faixa par tem que continuar
    parecendo permutada, e o hover tem que vencer as duas."""
    css = FOLHA.read_text(encoding="utf-8")
    zebra = css.index(".tabela tbody tr:nth-child(even) td")
    permutado = css.index(".tabela tbody tr.permutado td")
    hover = css.index(".tabela tbody tr:hover td")
    assert zebra < permutado < hover


def test_hover_da_tabela_nao_e_mais_cinza():
    css = FOLHA.read_text(encoding="utf-8")
    assert ".tabela tbody tr:hover td { background:var(--hover-linha); }" in css


def test_negrito_so_no_cabecalho_e_na_coluna_que_identifica():
    """Negrito em tudo vira mancha numa tabela de 139 linhas."""
    css = FOLHA.read_text(encoding="utf-8")
    assert ".tabela tbody td:first-child { font-weight:600; }" in css
    assert ".tabela td { border-bottom" in css and "font-weight:700" not in css.split(
        ".tabela td {")[1].split("}")[0]


def test_impressao_nao_ganhou_zebra():
    """A impressora da OM pode ser monocromática — e fundo custa toner (regra 12)."""
    impressao = (FOLHA.parent / "impressao.css").read_text(encoding="utf-8")
    assert "nth-child" not in impressao


# --- 5. rodapé ----------------------------------------------------------------
def test_rodape_traz_sistema_versao_e_om(client):
    from app import VERSAO
    texto = client.get("/").text
    assert VERSAO in texto and "Consulta aberta" in texto
