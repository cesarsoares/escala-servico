"""Testes da autenticação da gestão (regra 11).

Cobre o hash bcrypt + JWT (unidade) e o fluxo HTTP de login e rota protegida.
A rota /api/auth/me exige token; sem ele responde 401 (gestão fechada), enquanto
a consulta segue aberta (regra 13.1).
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.security import criar_token, hash_senha, ler_token, verificar_senha
from app.seeds.usuario import criar_ou_atualizar_gestor


# --- unidade: senha e token ---
def test_hash_verifica_senha():
    h = hash_senha("segredo-forte")
    assert h != "segredo-forte"
    assert verificar_senha("segredo-forte", h)
    assert not verificar_senha("errada", h)


def test_senha_longa_nao_estoura():
    # bcrypt 5 lança acima de 72 bytes; o módulo trunca antes.
    longa = "a" * 200
    assert verificar_senha(longa, hash_senha(longa))


def test_hash_malformado_nao_estoura():
    assert not verificar_senha("x", "não-é-um-hash-bcrypt")


def test_token_ida_e_volta():
    assert ler_token(criar_token(42)) == 42


def test_token_invalido_lanca():
    import jwt
    with pytest.raises(jwt.PyJWTError):
        ler_token("token.falso.aqui")


# --- integração HTTP ---
@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    criar_ou_atualizar_gestor(db, "brigada", "senha-boa-123", "Sgt Brigada")

    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    db.close()


def _login(client, usuario="brigada", senha="senha-boa-123"):
    return client.post("/api/auth/login", data={"username": usuario, "password": senha})


def test_login_ok_retorna_token(client):
    r = _login(client)
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_senha_errada_401(client):
    assert _login(client, senha="chute").status_code == 401


def test_login_usuario_inexistente_401(client):
    assert _login(client, usuario="ninguem").status_code == 401


def test_login_ignora_espaco_no_nome_de_usuario(client):
    """Copiar 'brigada ' de um documento é comum; o espaço não pode virar
    'login ou senha inválidos' sem explicação."""
    assert _login(client, usuario=" brigada ").status_code == 200


def test_login_nao_apara_a_senha(client):
    """Aparar caractere de senha seria aceitar uma credencial diferente."""
    assert _login(client, senha=" senha-boa-123 ").status_code == 401


def test_me_sem_token_401(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_com_bearer_ok(client):
    token = _login(client).json()["access_token"]
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["login"] == "brigada"
    assert "senha_hash" not in body


def test_me_com_cookie_ok(client):
    token = _login(client).json()["access_token"]
    client.cookies.set("access_token", token)
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["login"] == "brigada"


def test_me_token_lixo_401(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer nada.aqui.vale"})
    assert r.status_code == 401
