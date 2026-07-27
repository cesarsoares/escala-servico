"""Segurança da gestão (regra 11): hash de senha e token JWT.

A consulta é aberta (regra 13.1); só a gestão exige login. Usa bcrypt direto
(passlib 1.7 é incompatível com bcrypt >= 4.1) e PyJWT assinado com
`settings.secret_key`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

# bcrypt opera sobre no máximo 72 bytes e, na 5.x, LANÇA acima disso — truncamos
# explicitamente (senha longa vira sua versão de 72 bytes, de forma estável).
_BCRYPT_MAX_BYTES = 72


def _codificar(senha: str) -> bytes:
    return senha.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_senha(senha: str) -> str:
    """Gera o hash bcrypt (com salt) da senha em claro."""
    return bcrypt.hashpw(_codificar(senha), bcrypt.gensalt()).decode("ascii")


def verificar_senha(senha: str, senha_hash: str) -> bool:
    """Confere a senha em claro contra o hash armazenado."""
    try:
        return bcrypt.checkpw(_codificar(senha), senha_hash.encode("ascii"))
    except ValueError:
        # hash malformado no banco — trata como não-confere em vez de estourar
        return False


# Hash bcrypt válido de uma senha impossível. O login confere contra ele quando
# o usuário NÃO existe, para gastar o mesmo tempo de bcrypt de um login real e
# não vazar por tempo de resposta quais logins existem (enumeração de usuários).
_HASH_DUMMY = hash_senha("\x00usuario-inexistente\x00")


def hash_dummy() -> str:
    """Hash de referência para verificação em tempo constante de login ausente."""
    return _HASH_DUMMY


def criar_token(usuario_id: int, *, expira_min: int | None = None) -> str:
    """Emite um JWT cujo `sub` é o id do gestor (regra 11: múltiplos gestores)."""
    minutos = settings.token_expira_min if expira_min is None else expira_min
    agora = datetime.now(timezone.utc)
    payload = {
        "sub": str(usuario_id),
        "iat": agora,
        "exp": agora + timedelta(minutes=minutos),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algoritmo)


def ler_token(token: str) -> int:
    """Valida o token e devolve o id do gestor. Lança se inválido/expirado."""
    dados = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algoritmo])
    return int(dados["sub"])
