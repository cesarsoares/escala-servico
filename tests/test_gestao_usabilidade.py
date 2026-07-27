"""Usabilidade da gestão: busca, confirmação de ação e listas navegáveis.

Três defeitos que o uso real expõe e nenhum CSS resolve:
  1. achar uma pessoa entre centenas exigia rolar a página;
  2. gravar não dizia nada — só o ERRO tinha mensagem;
  3. `<select>` com o efetivo inteiro numa lista corrida.
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.escala import Escala, Participacao, Posto
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao, TipoImpedimento
from app.seeds import seed_circulos, seed_postos_graduacao, seed_tipos_impedimento
from app.seeds.usuario import criar_ou_atualizar_gestor
from app.web import AVISOS


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
    s.add(OrganizacaoMilitar(id=2, nome="Batalhão de Apoio", sigla="B Ap"))
    criar_ou_atualizar_gestor(s, "brigada", "senha-boa-123", "Sgt Brigada")
    s.flush()
    sgt = s.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "2º Sgt"))
    cap = s.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "Cap"))
    s.add(Militar(id=1, nome_guerra="SOUZA", nome_completo="José de Souza",
                  posto_graduacao_id=sgt, om_id=1, numero_antiguidade=10))
    s.add(Militar(id=2, nome_guerra="PEREIRA", nome_completo="Ana Pereira",
                  posto_graduacao_id=cap, om_id=2))
    s.add(Militar(id=3, nome_guerra="ALMEIDA", nome_completo="Carlos Souza Almeida",
                  posto_graduacao_id=cap, om_id=1))
    s.add(Escala(id=1, nome="Oficial de Dia", folga_minima_horas=48))
    s.flush()
    s.add(Posto(escala_id=1, ordem=1))
    s.add(Participacao(militar_id=1, escala_id=1))
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


def _linhas(texto: str) -> set[str]:
    """Nomes de guerra presentes na TABELA (o select de filtro não conta)."""
    return {n for n in ("SOUZA", "PEREIRA", "ALMEIDA")
            if f'>{n}</a>' in texto}


# --- 1. Busca no efetivo ------------------------------------------------------
def test_busca_por_nome_de_guerra(client):
    assert _linhas(client.get("/gestao/militares?q=souza").text) == {"SOUZA", "ALMEIDA"}


def test_busca_ignora_maiusculas(client):
    """Quem procura digita 'souza', não 'SOUZA'."""
    assert _linhas(client.get("/gestao/militares?q=SoUzA").text) == {"SOUZA", "ALMEIDA"}


def test_busca_casa_tambem_o_nome_completo(client):
    """ALMEIDA só é encontrado por 'souza' porque está no nome completo."""
    assert "ALMEIDA" in _linhas(client.get("/gestao/militares?q=Souza Almeida").text)


def test_busca_por_parte_do_nome(client):
    assert _linhas(client.get("/gestao/militares?q=erei").text) == {"PEREIRA"}


def test_filtro_por_posto_graduacao(client, db):
    cap = db.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "Cap"))
    assert _linhas(client.get(f"/gestao/militares?posto_graduacao_id={cap}").text) \
        == {"PEREIRA", "ALMEIDA"}


def test_filtro_por_om(client):
    assert _linhas(client.get("/gestao/militares?om_id=2").text) == {"PEREIRA"}


def test_filtros_combinam(client, db):
    cap = db.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "Cap"))
    assert _linhas(client.get(f"/gestao/militares?q=souza&posto_graduacao_id={cap}").text) \
        == {"ALMEIDA"}


def test_busca_mostra_quantos_de_quantos(client):
    r = client.get("/gestao/militares?q=souza")
    assert "de 3 militares" in r.text          # 2 achados de 3 ativos


def test_busca_sem_resultado_explica(client):
    r = client.get("/gestao/militares?q=ninguem")
    assert "Nenhum militar encontrado" in r.text


def test_filtro_invalido_nao_derruba_a_tela(client):
    r = client.get("/gestao/militares?posto_graduacao_id=abc&om_id=")
    assert r.status_code == 200 and _linhas(r.text) == {"SOUZA", "PEREIRA", "ALMEIDA"}


def test_busca_preserva_a_lista_de_inativos(client, db):
    db.get(Militar, 2).ativo = False
    db.commit()
    r = client.get("/gestao/militares?inativos=1&q=pereira")
    assert _linhas(r.text) == {"PEREIRA"}


# --- 2. Confirmação de ação ---------------------------------------------------
def test_toda_acao_termina_com_confirmacao(client, db):
    """Antes, gravar era silencioso: só o erro falava."""
    r = client.post("/gestao/militares", follow_redirects=False, data={
        "nome_guerra": "NOVO", "nome_completo": "Militar Novo",
        "posto_graduacao_id": db.scalar(select(PostoGraduacao.id)), "om_id": 1,
        "identidade": "", "cpf": "", "data_promocao": "", "data_praca": "",
        "data_nascimento": "", "numero_antiguidade": "",
    })
    assert r.status_code == 303
    assert r.headers["location"] == "/gestao/militares?ok=militar-criado"
    # e a tela de destino mostra a mensagem
    assert AVISOS["militar-criado"] in client.get(r.headers["location"]).text


def test_confirmacao_desconhecida_e_ignorada(client):
    """A chave vem da URL: inventar uma não pode injetar texto na tela."""
    r = client.get("/gestao/militares?ok=<script>alerta</script>")
    assert r.status_code == 200
    assert "<script>alerta</script>" not in r.text


def test_sem_chave_nao_mostra_faixa_de_sucesso(client):
    assert 'class="sucesso"' not in client.get("/gestao/militares").text


def test_desativar_e_reativar_confirmam(client):
    r = client.post("/gestao/militares/1/desativar", follow_redirects=False)
    assert r.headers["location"].endswith("ok=militar-desativado")
    r = client.post("/gestao/militares/1/reativar", follow_redirects=False)
    assert r.headers["location"].endswith("ok=militar-reativado")


def test_todas_as_chaves_tem_mensagem():
    """Chave sem tradução viraria um redirect que não mostra nada."""
    import re
    from pathlib import Path
    usadas = set()
    for arq in ("app/web/gestao.py", "app/web/gestao_escalas.py"):
        texto = Path(arq).read_text(encoding="utf-8")
        usadas |= set(re.findall(r'\?ok=([a-z-]+)', texto))
        usadas |= set(re.findall(r'&ok=([a-z-]+)', texto))
        usadas |= set(re.findall(r'_volta_para\([^,]+, "([a-z-]+)"\)', texto))
    assert usadas, "nenhuma chave encontrada — o teste perdeu o alvo"
    assert usadas <= set(AVISOS)


# --- 3. Selects agrupados por posto/graduação ---------------------------------
def test_select_de_impedimento_agrupa_por_posto(client):
    r = client.get("/gestao/impedimentos")
    assert '<optgroup label="Cap">' in r.text
    assert '<optgroup label="2º Sgt">' in r.text


def test_grupos_saem_na_ordem_hierarquica(client):
    """Capitão antes de sargento — alfabética não quer dizer nada aqui."""
    texto = client.get("/gestao/impedimentos").text
    assert texto.index('label="Cap"') < texto.index('label="2º Sgt"')


def test_agrupar_por_posto_e_estavel(db):
    from app.web.gestao import agrupar_por_posto
    militares = db.scalars(
        select(Militar).order_by(Militar.nome_guerra)).all()
    grupos = agrupar_por_posto(militares)
    assert [sigla for sigla, _ in grupos] == ["Cap", "2º Sgt"]
    assert [m.nome_guerra for m in grupos[0][1]] == ["ALMEIDA", "PEREIRA"]
