"""Recuperação de senha do gestor (demanda do Brigada, 30/07).

"Esqueci a senha que coloquei na instalação." Sem e-mail e sem pergunta secreta,
quem prova o direito de redefinir é **quem alcança o servidor**: o código vai
para um arquivo ao lado do banco, como a senha de primeiro acesso.

O que estes testes guardam, em ordem de gravidade: o código NÃO aparece na tela
(exibi-lo anularia a trava), ele vence, ele morre ao ser usado, e pedir de novo
dentro da validade não invalida o que a TI acabou de ler.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.gestao import Auditoria, Usuario
from app.security import verificar_senha
from app.seeds.usuario import criar_ou_atualizar_gestor
from app.services import recuperacao

SENHA_ANTIGA = "senha-boa-123"
SENHA_NOVA = "outra-senha-456"


@pytest.fixture(autouse=True)
def arquivo_isolado(tmp_path, monkeypatch):
    """O código NUNCA pode ser escrito na pasta do projeto durante a suíte.

    Mesmo cuidado do `backup_automatico` no conftest: o default aponta para
    `dados/`, e rodar pytest não pode deixar credencial viva no repositório.
    """
    monkeypatch.setattr(settings, "recuperacao_file", str(tmp_path / "recuperar.txt"))
    monkeypatch.setattr(settings, "recuperacao_validade_min", 60)
    yield


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = Session(engine)
    criar_ou_atualizar_gestor(s, "brigada", SENHA_ANTIGA, "Sgt Brigada")
    s.commit()
    yield s
    s.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _codigo(db) -> str:
    return recuperacao.pedir(db).valor


# --- serviço ----------------------------------------------------------------

def test_codigo_e_gravado_no_arquivo(db):
    c = recuperacao.pedir(db)
    assert c.caminho.is_file()
    assert c.valor in c.caminho.read_text(encoding="utf-8")
    assert c.novo is True


def test_pedir_de_novo_devolve_o_mesmo_codigo(db):
    """Senão qualquer um na rede recarregando a página invalidaria o código que
    a TI acabou de ler ao telefone."""
    primeiro = recuperacao.pedir(db)
    segundo = recuperacao.pedir(db)
    assert segundo.valor == primeiro.valor
    assert segundo.novo is False


def test_codigo_vencido_nao_confere(db, monkeypatch):
    codigo = _codigo(db)
    assert recuperacao.conferir(codigo) is True
    # empurra o relógio para depois do vencimento
    depois = datetime.now(timezone.utc) + timedelta(minutes=61)
    monkeypatch.setattr(recuperacao, "_agora", lambda: depois)
    assert recuperacao.conferir(codigo) is False


def test_codigo_vencido_da_lugar_a_outro(db, monkeypatch):
    velho = _codigo(db)
    depois = datetime.now(timezone.utc) + timedelta(minutes=61)
    monkeypatch.setattr(recuperacao, "_agora", lambda: depois)
    novo = recuperacao.pedir(db)
    assert novo.valor != velho and novo.novo is True


def test_sem_codigo_nada_confere(db):
    assert recuperacao.conferir("qualquer-coisa") is False
    assert recuperacao.conferir("") is False


def test_arquivo_corrompido_e_tratado_como_ausente(db):
    c = recuperacao.pedir(db)
    c.caminho.write_text("lixo que não é json", encoding="utf-8")
    assert recuperacao.conferir(c.valor) is False
    assert recuperacao.pedir(db).novo is True     # o pedido seguinte grava outro


def test_redefinir_troca_a_senha_e_apaga_o_codigo(db):
    codigo = _codigo(db)
    recuperacao.redefinir(db, codigo, "brigada", SENHA_NOVA, SENHA_NOVA)
    u = db.scalar(select(Usuario).where(Usuario.login == "brigada"))
    assert verificar_senha(SENHA_NOVA, u.senha_hash)
    assert not verificar_senha(SENHA_ANTIGA, u.senha_hash)
    assert not recuperacao._arquivo().is_file()


def test_codigo_serve_uma_vez_so(db):
    codigo = _codigo(db)
    recuperacao.redefinir(db, codigo, "brigada", SENHA_NOVA, SENHA_NOVA)
    with pytest.raises(recuperacao.RecuperacaoNegada, match="inválido"):
        recuperacao.redefinir(db, codigo, "brigada", "terceira-senha", "terceira-senha")


def test_redefinir_sem_codigo_e_negado(db):
    with pytest.raises(recuperacao.RecuperacaoNegada, match="inválido"):
        recuperacao.redefinir(db, "chute", "brigada", SENHA_NOVA, SENHA_NOVA)


def test_login_inexistente_e_negado(db):
    with pytest.raises(recuperacao.RecuperacaoNegada, match="login"):
        recuperacao.redefinir(db, _codigo(db), "ninguem", SENHA_NOVA, SENHA_NOVA)


def test_senhas_diferentes_sao_negadas(db):
    with pytest.raises(recuperacao.RecuperacaoNegada, match="conferem"):
        recuperacao.redefinir(db, _codigo(db), "brigada", SENHA_NOVA, "outra")


def test_senha_curta_e_negada(db):
    with pytest.raises(recuperacao.RecuperacaoNegada, match="caracteres"):
        recuperacao.redefinir(db, _codigo(db), "brigada", "abc", "abc")


def test_senha_curta_nao_gasta_o_codigo(db):
    """A validação da senha vem DEPOIS do código, mas errar a senha não pode
    custar uma segunda ida ao servidor."""
    codigo = _codigo(db)
    with pytest.raises(recuperacao.RecuperacaoNegada):
        recuperacao.redefinir(db, codigo, "brigada", "abc", "abc")
    recuperacao.redefinir(db, codigo, "brigada", SENHA_NOVA, SENHA_NOVA)


def test_gestor_desativado_por_engano_volta(db):
    """Quem tem o código já provou acesso ao servidor — mais do que o gestor tem."""
    u = db.scalar(select(Usuario).where(Usuario.login == "brigada"))
    u.ativo = False
    db.commit()
    recuperacao.redefinir(db, _codigo(db), "brigada", SENHA_NOVA, SENHA_NOVA)
    db.expire_all()
    assert db.scalar(select(Usuario).where(Usuario.login == "brigada")).ativo is True


# --- telas ------------------------------------------------------------------

def test_tela_abre_sem_login(client):
    r = client.get("/gestao/recuperar-senha")
    assert r.status_code == 200
    assert "Esqueci a senha" in r.text


def test_pedir_nao_mostra_o_codigo_na_tela(client, db):
    """A trava inteira depende disto: o código prova acesso ao SERVIDOR."""
    r = client.post("/gestao/recuperar-senha/pedir")
    assert r.status_code == 200
    valor = recuperacao._ler()[0]
    assert valor not in r.text
    assert settings.recuperacao_file in r.text     # mas diz ONDE está


def test_tela_inicial_tambem_nao_vaza_codigo_existente(client, db):
    valor = _codigo(db)
    r = client.get("/gestao/recuperar-senha")
    assert valor not in r.text


def test_fluxo_completo_pela_tela(client, db):
    client.post("/gestao/recuperar-senha/pedir")
    codigo = recuperacao._ler()[0]
    r = client.post("/gestao/recuperar-senha", data={
        "codigo": codigo, "login": "brigada",
        "senha": SENHA_NOVA, "senha2": SENHA_NOVA,
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/gestao/login?ok=senha-recuperada"
    # e a senha nova entra
    entrada = client.post("/gestao/login",
                          data={"username": "brigada", "password": SENHA_NOVA},
                          follow_redirects=False)
    assert entrada.status_code == 303


def test_codigo_errado_devolve_400_com_o_motivo(client, db):
    _codigo(db)
    r = client.post("/gestao/recuperar-senha", data={
        "codigo": "chute", "login": "brigada",
        "senha": SENHA_NOVA, "senha2": SENHA_NOVA,
    })
    assert r.status_code == 400
    assert "inválido" in r.text
    db.expire_all()
    u = db.scalar(select(Usuario).where(Usuario.login == "brigada"))
    assert verificar_senha(SENHA_ANTIGA, u.senha_hash)


def test_erro_nao_devolve_a_senha_digitada(client, db):
    """Devolver credencial no HTML a deixa no cache e no histórico da máquina."""
    r = client.post("/gestao/recuperar-senha", data={
        "codigo": "chute", "login": "brigada",
        "senha": "senha-secreta-999", "senha2": "senha-secreta-999",
    })
    assert "senha-secreta-999" not in r.text
    assert "chute" not in r.text
    assert 'value="brigada"' in r.text      # o login volta, que não é segredo


def test_recuperacao_e_auditada(client, db):
    client.post("/gestao/recuperar-senha/pedir")
    client.post("/gestao/recuperar-senha", data={
        "codigo": recuperacao._ler()[0], "login": "brigada",
        "senha": SENHA_NOVA, "senha2": SENHA_NOVA,
    }, follow_redirects=False)
    reg = db.scalar(select(Auditoria).where(Auditoria.acao == "recuperar-senha"))
    assert reg is not None
    assert reg.dados_depois["login"] == "brigada"
    assert "senha" not in str(reg.dados_depois).lower().replace("recuperar-senha", "")


def test_instalacao_sem_gestor_vai_para_o_primeiro_acesso(client, db):
    """Banco vazio não recupera nada: o caminho é criar o primeiro acesso."""
    db.query(Usuario).delete()
    db.commit()
    r = client.get("/gestao/recuperar-senha", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/gestao/primeiro-acesso"


def test_login_oferece_o_caminho(client):
    assert "/gestao/recuperar-senha" in client.get("/gestao/login").text


def test_aviso_de_sucesso_tem_traducao(client):
    from app.web import AVISOS
    assert "senha-recuperada" in AVISOS
    assert AVISOS["senha-recuperada"] in client.get("/gestao/login?ok=senha-recuperada").text
