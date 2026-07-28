"""Senha de primeiro acesso: fecha a janela em que qualquer um vira gestor.

Entre `docker compose up` (ou o boot do Windows) e o gestor ser criado, as
telas de primeiro acesso e de restauração ficam abertas a quem alcança a porta
8000 — numa rede de OM, o efetivo inteiro. Instalado na sexta e usado na
segunda, é um fim de semana de porta encostada.

O conserto é o padrão do Jenkins: senha aleatória gerada no primeiro boot,
gravada ao lado do banco e anunciada no log. Quem instalou lê o arquivo; quem
só alcança a rede, não.

O que estes testes protegem:

  1. sem a senha certa **não se cria gestor nem se restaura backup**;
  2. a senha **some** assim que existe gestor — credencial viva não fica para
     trás;
  3. o anúncio do arranque só fala quando há o que dizer.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.gestao import Usuario
from app.seeds import seed_circulos, seed_postos_graduacao
from app.services import instalacao


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = Session(engine)
    seed_circulos(s)
    seed_postos_graduacao(s)
    s.commit()
    yield s
    s.close()


@pytest.fixture()
def senha_em(tmp_path, monkeypatch) -> Path:
    """Aponta o arquivo da senha para um temporário — nunca o do projeto."""
    alvo = tmp_path / "primeiro-acesso.txt"
    monkeypatch.setattr(settings, "primeiro_acesso_file", str(alvo))
    return alvo


@pytest.fixture()
def client(db, senha_em):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --- a senha em si ------------------------------------------------------------
def test_a_senha_e_criada_uma_vez_e_persiste(senha_em):
    """Estável entre chamadas: o TI lê o arquivo e digita na tela."""
    primeira = instalacao.senha_primeiro_acesso()
    assert primeira and len(primeira) >= 12
    assert instalacao.senha_primeiro_acesso() == primeira
    assert primeira in senha_em.read_text(encoding="utf-8")


def test_o_arquivo_explica_o_que_e(senha_em):
    """Quem abre o .txt seis meses depois precisa saber o que está olhando."""
    instalacao.senha_primeiro_acesso()
    texto = senha_em.read_text(encoding="utf-8")
    assert "PRIMEIRO ACESSO" in texto
    assert "apagado" in texto            # diz que some sozinho
    # Os comentários não podem ser confundidos com a senha.
    assert instalacao.senha_primeiro_acesso() not in texto.split("\n")[0]


def test_apagar_o_arquivo_gera_outra_senha(senha_em):
    """É a recuperação documentada: perdeu, apaga e recarrega a página."""
    primeira = instalacao.senha_primeiro_acesso()
    senha_em.unlink()
    assert instalacao.senha_primeiro_acesso() != primeira


def test_conferir_recusa_o_que_nao_e_a_senha(senha_em):
    certa = instalacao.senha_primeiro_acesso()
    assert instalacao.conferir_senha(certa) is True
    assert instalacao.conferir_senha(f"  {certa}  ") is True      # colar traz espaço
    assert instalacao.conferir_senha("") is False
    assert instalacao.conferir_senha(certa[:-1]) is False
    assert instalacao.conferir_senha(certa + "x") is False


def test_sem_onde_gravar_o_sistema_sobe_com_senha_de_processo(monkeypatch, tmp_path):
    """Volume só de leitura não pode impedir o sistema de subir — mas jamais
    com senha adivinhável."""
    monkeypatch.setattr(settings, "primeiro_acesso_file",
                        str(tmp_path / "nao-existe" / "x.txt"))
    monkeypatch.setattr(Path, "write_text",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("só leitura")))
    senha = instalacao.senha_primeiro_acesso()
    assert senha and len(senha) >= 12


# --- a porta do primeiro gestor ----------------------------------------------
def _criar_gestor(client, senha_instalacao: str):
    return client.post("/gestao/primeiro-acesso", data={
        "senha_instalacao": senha_instalacao,
        "login": "brigada", "nome": "Sgt Brigada",
        "senha": "escala-2026", "senha2": "escala-2026",
    }, follow_redirects=False)


def test_sem_a_senha_ninguem_vira_gestor(client, db):
    """O ponto inteiro: alcançar a porta 8000 não basta para virar administrador."""
    r = _criar_gestor(client, "chute")
    assert r.status_code == 400
    assert "Senha de instalação incorreta" in r.text
    assert db.scalars(select(Usuario)).all() == []


def test_com_a_senha_certa_o_gestor_e_criado_e_a_senha_some(client, db, senha_em):
    r = _criar_gestor(client, instalacao.senha_primeiro_acesso())
    assert r.status_code == 303
    assert r.headers["location"] == "/gestao/instalacao?ok=primeiro-gestor"
    assert db.scalar(select(Usuario.login)) == "brigada"
    # Credencial viva não fica para trás: seria uma segunda porta para sempre.
    assert not senha_em.exists()


def test_a_tela_diz_onde_esta_a_senha(client, senha_em):
    """Sem isto, a trava vira parede: ninguém adivinha onde procurar."""
    corpo = client.get("/gestao/primeiro-acesso").text
    assert "Senha de instalação" in corpo
    assert senha_em.name in corpo
    # E abrir a tela já cria o arquivo, para quem subiu o sistema à mão.
    assert senha_em.is_file()


def test_a_senha_nao_aparece_na_tela(client, senha_em):
    """Ela prova acesso ao SERVIDOR — exibi-la na página anularia a trava."""
    senha = instalacao.senha_primeiro_acesso()
    assert senha not in client.get("/gestao/primeiro-acesso").text


def test_com_gestor_a_porta_fecha_mesmo_com_a_senha_certa(client, db, senha_em):
    """A senha não é um bypass permanente: vale só até existir gestor."""
    senha = instalacao.senha_primeiro_acesso()
    _criar_gestor(client, senha)
    r = _criar_gestor(client, senha)
    assert r.status_code == 303 and r.headers["location"] == "/gestao/login"
    assert len(db.scalars(select(Usuario)).all()) == 1


# --- a porta da restauração (máquina nova) -----------------------------------
def test_restaurar_na_maquina_nova_tambem_exige_a_senha(client, tmp_path, monkeypatch):
    """Restaurar substitui a instalação inteira — nem pensar sem prova de acesso."""
    monkeypatch.setattr(settings, "database_url",
                        f"sqlite+pysqlite:///{(tmp_path / 'e.sqlite3').as_posix()}")
    r = client.post("/gestao/restaurar-instalacao",
                    data={"senha_instalacao": "chute"},
                    files={"arquivo": ("bk.sqlite3", b"qualquer coisa",
                                       "application/octet-stream")})
    assert r.status_code == 400
    assert "Senha de instalação incorreta" in r.text


def test_a_senha_e_conferida_TAMBEM_na_confirmacao(client, senha_em):
    """A etapa 1 não deixa credencial de pé: chegar direto na 2 não passa."""
    r = client.post("/gestao/restaurar-instalacao/confirmar",
                    data={"token": "qualquer", "senha_instalacao": "chute"})
    assert r.status_code == 400
    assert "Senha de instalação incorreta" in r.text


def test_a_tela_de_restauracao_pede_a_senha(client, senha_em):
    corpo = client.get("/gestao/restaurar-instalacao").text
    assert "Senha de instalação" in corpo
    assert senha_em.name in corpo


# --- o anúncio do arranque ----------------------------------------------------
def test_o_arranque_anuncia_a_senha_quando_nao_ha_gestor(db, senha_em, capsys):
    """É o único momento em que a TI está olhando a saída."""
    senha = instalacao.anunciar_primeiro_acesso(db)
    saida = capsys.readouterr().out
    assert senha is not None
    assert senha in saida
    assert "PRIMEIRO ACESSO" in saida
    assert "ANTES de expor" in saida          # diz o que fazer, não só o quê


def test_o_arranque_cala_e_limpa_quando_ja_ha_gestor(db, senha_em, capsys):
    """Instalação de meses atrás não relembra senha nenhuma no log."""
    instalacao.senha_primeiro_acesso()          # sobrou de uma tentativa
    db.add(Usuario(login="brigada", senha_hash="x", nome="Sgt Brigada"))
    db.commit()

    assert instalacao.anunciar_primeiro_acesso(db) is None
    assert capsys.readouterr().out == ""
    assert not senha_em.exists()
