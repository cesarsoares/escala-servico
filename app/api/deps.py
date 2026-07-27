"""Dependências de autenticação da gestão (regra 11).

`get_current_user` protege os endpoints de gestão. A consulta segue aberta
(regra 13.1) e não usa isto. O token vem no cabeçalho `Authorization: Bearer`
(APIs/Swagger) ou no cookie `access_token` (telas Jinja futuras).
"""
from __future__ import annotations

import jwt
from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.gestao import Usuario
from app.security import ler_token

# auto_error=False: sem cabeçalho não estoura aqui — ainda tentamos o cookie.
_oauth2 = OAuth2PasswordBearer(tokenUrl="api/auth/login", auto_error=False)

_NAO_AUTENTICADO = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="não autenticado",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    header_token: str | None = Depends(_oauth2),
    access_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> Usuario:
    """Resolve o gestor logado a partir do token; 401 se ausente/inválido."""
    token = header_token or access_token
    if not token:
        raise _NAO_AUTENTICADO
    try:
        usuario_id = ler_token(token)
    except jwt.PyJWTError:
        raise _NAO_AUTENTICADO

    usuario = db.get(Usuario, usuario_id)
    if usuario is None or not usuario.ativo:
        raise _NAO_AUTENTICADO
    return usuario
