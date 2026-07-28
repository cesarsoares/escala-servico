"""Instalação numa OM nova: primeiro acesso, chave de sessão e assistente.

Três atritos de quem recebe a imagem e precisa pôr o sistema no ar:

  1. sem gestor no banco não havia como entrar, a não ser pela linha de comando
     dentro do container;
  2. a chave que assina a sessão tinha um valor padrão — e o código é público;
  3. instalado, o sistema não dizia por onde começar.
"""
from datetime import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import chave_persistente
from app.database import Base, get_db
from app.main import app
from app.models.escala import Escala, Participacao, Posto
from app.models.gestao import Auditoria, Usuario
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.seeds import seed_circulos, seed_postos_graduacao
from app.seeds.usuario import criar_ou_atualizar_gestor
from app.services import instalacao


@pytest.fixture()
def db():
    """Instalação recém-subida: só as tabelas e os dados de referência."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = Session(engine)
    seed_circulos(s)
    seed_postos_graduacao(s)
    s.commit()
    yield s
    s.close()


@pytest.fixture()
def senha_instalacao(tmp_path, monkeypatch) -> str:
    """A senha de primeiro acesso, num arquivo temporário.

    Ela fecha a janela em que qualquer um na rede vira gestor (ver
    `test_primeiro_acesso_senha.py`); aqui só precisa existir e ser conhecida,
    para que estes testes continuem exercitando o que sempre exercitaram.
    """
    from app.config import settings
    from app.services.instalacao import senha_primeiro_acesso

    monkeypatch.setattr(settings, "primeiro_acesso_file",
                        str(tmp_path / "primeiro-acesso.txt"))
    return senha_primeiro_acesso()


@pytest.fixture()
def client(db, senha_instalacao):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _povoar_ate_escala(db):
    """Leva a instalação até o mínimo utilizável: OM, efetivo e escala pronta."""
    db.add(OrganizacaoMilitar(id=1, nome="1º Batalhão", sigla="1º BI", propria=True))
    db.flush()
    sgt = db.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "2º Sgt"))
    db.add(Militar(id=1, nome_guerra="SANTIAGO", nome_completo="SANTIAGO DA SILVA",
                   posto_graduacao_id=sgt, om_id=1, numero_antiguidade=10))
    db.add(Escala(id=1, nome="Oficial de Dia"))
    db.flush()
    db.add(Posto(escala_id=1, ordem=1))
    db.add(Participacao(militar_id=1, escala_id=1))
    db.commit()


# --- 1. Primeiro acesso pela tela (regra 11) ---------------------------------
def test_sem_gestor_o_login_manda_para_o_primeiro_acesso(client):
    r = client.get("/gestao/login", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/gestao/primeiro-acesso"


def test_gestao_sem_sessao_chega_ao_primeiro_acesso(client):
    """Quem abre /gestao numa instalação nova tem de terminar na tela certa."""
    r = client.get("/gestao", follow_redirects=True)
    assert r.status_code == 200 and "Primeiro acesso" in r.text


def test_cria_o_primeiro_gestor_e_ja_entra_logado(client, db, senha_instalacao):
    r = client.post("/gestao/primeiro-acesso", follow_redirects=False, data={
        "senha_instalacao": senha_instalacao,
        "login": " Brigada ", "nome": "Sgt Brigada",
        "senha": "senha-boa-123", "senha2": "senha-boa-123",
    })
    assert r.status_code == 303
    assert r.headers["location"] == "/gestao/instalacao?ok=primeiro-gestor"
    assert "access_token" in r.cookies                      # entrou sem passar pelo login
    usuario = db.scalar(select(Usuario))
    assert usuario.login == "brigada" and usuario.ativo     # login normalizado
    # o painel abre com a sessão recém-criada
    assert client.get("/gestao/instalacao").status_code == 200


def test_a_criacao_do_primeiro_gestor_fica_auditada(client, db, senha_instalacao):
    client.post("/gestao/primeiro-acesso", data={
        "senha_instalacao": senha_instalacao,
        "login": "brigada", "nome": "Sgt Brigada",
        "senha": "senha-boa-123", "senha2": "senha-boa-123"})
    registro = db.scalar(select(Auditoria).where(Auditoria.entidade == "usuario"))
    assert registro is not None and registro.acao == "criar"
    assert "senha_hash" not in (registro.dados_depois or {})   # senha não vaza (regra 11)


def test_senha_curta_ou_diferente_e_recusada_sem_criar_ninguem(client, db, senha_instalacao):
    curta = client.post("/gestao/primeiro-acesso", data={
        "senha_instalacao": senha_instalacao,
        "login": "brigada", "nome": "Sgt", "senha": "123", "senha2": "123"})
    assert curta.status_code == 400
    diferentes = client.post("/gestao/primeiro-acesso", data={
        "senha_instalacao": senha_instalacao,
        "login": "brigada", "nome": "Sgt", "senha": "senha-boa-123", "senha2": "outra-senha"})
    assert diferentes.status_code == 400 and "não conferem" in diferentes.text
    assert db.scalar(select(Usuario)) is None


def test_o_que_foi_digitado_volta_no_erro(client, senha_instalacao):
    r = client.post("/gestao/primeiro-acesso", data={
        "senha_instalacao": senha_instalacao,
        "login": "brigada", "nome": "Sgt Brigada", "senha": "123", "senha2": "123"})
    assert 'value="brigada"' in r.text and 'value="Sgt Brigada"' in r.text


def test_com_gestor_a_porta_se_fecha(client, db):
    """A tela não pode virar cadastro aberto de gestores."""
    criar_ou_atualizar_gestor(db, "brigada", "senha-boa-123", "Sgt Brigada")
    db.commit()
    assert client.get("/gestao/primeiro-acesso", follow_redirects=False
                      ).headers["location"] == "/gestao/login"
    r = client.post("/gestao/primeiro-acesso", follow_redirects=False, data={
        "login": "intruso", "nome": "Intruso",
        "senha": "senha-boa-123", "senha2": "senha-boa-123"})
    assert r.headers["location"] == "/gestao/login"
    assert db.scalar(select(Usuario).where(Usuario.login == "intruso")) is None


def test_login_normal_volta_a_valer_depois(client, db):
    criar_ou_atualizar_gestor(db, "brigada", "senha-boa-123", "Sgt Brigada")
    db.commit()
    assert "Gestão da escala" in client.get("/gestao/login").text
    r = client.post("/gestao/login", follow_redirects=False,
                    data={"username": "brigada", "password": "senha-boa-123"})
    assert r.status_code == 303 and r.headers["location"] == "/gestao"


# --- 2. Chave de sessão gerada e persistida ----------------------------------
def test_a_chave_nasce_aleatoria_e_se_mantem(tmp_path):
    """Sem SECRET_KEY no ambiente, nenhuma instalação roda com chave conhecida."""
    caminho = str(tmp_path / "sub" / "secret_key")
    primeira = chave_persistente(caminho)
    assert len(primeira) >= 32
    assert chave_persistente(caminho) == primeira          # sobrevive ao restart
    assert Path(caminho).read_text(encoding="utf-8").strip() == primeira


def test_duas_instalacoes_nao_compartilham_chave(tmp_path):
    uma = chave_persistente(str(tmp_path / "a" / "secret_key"))
    outra = chave_persistente(str(tmp_path / "b" / "secret_key"))
    assert uma != outra


def test_sem_onde_gravar_o_sistema_sobe_com_chave_de_processo(tmp_path):
    """Volume só-leitura não pode derrubar a aplicação — nem devolver chave fixa."""
    ocupado = tmp_path / "arquivo"
    ocupado.write_text("x", encoding="utf-8")
    chave = chave_persistente(str(ocupado / "impossivel" / "secret_key"))
    assert len(chave) >= 32


def test_nao_ha_chave_padrao_no_codigo():
    """A chave publicada no GitHub deixaria forjar a sessão de qualquer OM."""
    fonte = Path("app/config.py").read_text(encoding="utf-8")
    assert "dev-inseguro" not in fonte
    assert 'secret_key: str = ""' in fonte


# --- 3. Assistente de instalação ---------------------------------------------
def test_instalacao_nova_tem_tudo_pendente_menos_as_graduacoes(db):
    lista = instalacao.passos(db)
    feitos = {p.chave for p in lista if p.feito}
    assert feitos == {"graduacoes"}                 # vêm do seed da Lei 6.880/80
    assert not instalacao.concluida(lista)
    assert instalacao.proximo(lista).chave == "instalacao"


def test_o_proximo_passo_respeita_a_ordem_de_dependencia(db):
    db.add(OrganizacaoMilitar(id=1, nome="1º Batalhão", sigla="1º BI", propria=True))
    db.commit()
    assert instalacao.proximo(instalacao.passos(db)).chave == "efetivo"


def test_escala_sem_posto_ou_sem_participante_nao_conta_como_pronta(db):
    _povoar_ate_escala(db)
    db.add(Escala(id=2, nome="Museu"))              # sem posto e sem participante
    db.commit()
    passo = next(p for p in instalacao.passos(db) if p.chave == "escalas")
    assert passo.feito and "1 pronta(s)" in passo.estado and "1 incompleta(s)" in passo.estado


def test_o_minimo_utilizavel_conclui_a_instalacao(db):
    """Histórico e segundo gestor são opcionais: uma OM sem passado em planilha
    nunca teria a instalação 'completa', e o aviso viraria ruído permanente."""
    _povoar_ate_escala(db)
    lista = instalacao.passos(db)
    assert instalacao.concluida(lista)
    assert {p.chave for p in instalacao.pendentes(lista)} == {"historico", "gestores"}


def test_a_faixa_do_painel_some_quando_a_instalacao_esta_completa(client, db):
    criar_ou_atualizar_gestor(db, "brigada", "senha-boa-123", "Sgt Brigada")
    db.commit()
    client.post("/gestao/login", data={"username": "brigada", "password": "senha-boa-123"})
    assert "Instalação em andamento" in client.get("/gestao").text
    _povoar_ate_escala(db)
    assert "Instalação em andamento" not in client.get("/gestao").text


def test_a_tela_do_assistente_mostra_o_proximo_passo(client, db):
    criar_ou_atualizar_gestor(db, "brigada", "senha-boa-123", "Sgt Brigada")
    db.commit()
    client.post("/gestao/login", data={"username": "brigada", "password": "senha-boa-123"})
    html = client.get("/gestao/instalacao").text
    assert "Próximo passo: Dizer qual é a OM desta casa" in html
    assert "/gestao/configuracao/instalacao" in html
    _povoar_ate_escala(db)
    assert "Instalação concluída" in client.get("/gestao/instalacao").text


def test_o_assistente_exige_sessao(client):
    r = client.get("/gestao/instalacao", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/gestao/login"
