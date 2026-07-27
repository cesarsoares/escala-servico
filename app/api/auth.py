"""Autenticação da gestão (regra 11): login e identidade do gestor logado.

A consulta é aberta (regra 13.1); estes endpoints emitem/consomem o token que
protege os demais endpoints de gestão.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.gestao import Usuario
from app.schemas.gestao import TokenOut, UsuarioOut
from app.security import criar_token, hash_dummy, verificar_senha

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Troca login+senha por um token JWT (form `username`/`password`)."""
    # login sem espaços das pontas (a senha NÃO — ver web/gestao.py::login)
    usuario = db.scalar(select(Usuario).where(Usuario.login == form.username.strip()))
    # verifica sempre a senha (contra um hash dummy quando não há usuário) para
    # gastar o mesmo tempo de bcrypt e não vazar por tempo quais logins existem
    hash_ref = usuario.senha_hash if usuario else hash_dummy()
    if not verificar_senha(form.password, hash_ref) or usuario is None or not usuario.ativo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="login ou senha inválidos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenOut(access_token=criar_token(usuario.id))


@router.get("/me", response_model=UsuarioOut)
def me(usuario: Usuario = Depends(get_current_user)):
    """Dados do gestor autenticado (sem a senha)."""
    return usuario
