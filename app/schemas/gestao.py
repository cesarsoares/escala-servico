"""Schemas de gestão e auditoria (regra 11).

A senha entra em claro (senha) e é hasheada na camada de serviço; nunca sai:
o Out expõe apenas dados públicos do usuário.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from app.schemas.base import Entrada, Resposta


# --- Usuário (gestor) ---
class UsuarioCreate(Entrada):
    login: str = Field(max_length=60, min_length=1)
    senha: str = Field(min_length=8)        # hasheada no serviço; jamais persistida em claro
    nome: str = Field(max_length=120)
    ativo: bool = True


class UsuarioUpdate(Entrada):
    login: str | None = Field(default=None, max_length=60, min_length=1)
    senha: str | None = Field(default=None, min_length=8)
    nome: str | None = Field(default=None, max_length=120)
    ativo: bool | None = None


class UsuarioOut(Resposta):
    id: int
    login: str
    nome: str
    ativo: bool
    # senha_hash deliberadamente ausente


# --- Autenticação (regra 11) ---
class TokenOut(Resposta):
    """Resposta do login: token JWT portador (Bearer)."""
    access_token: str
    token_type: str = "bearer"


# --- Auditoria (somente leitura) ---
class AuditoriaOut(Resposta):
    id: int
    usuario_id: int | None
    entidade: str
    entidade_id: int | None
    acao: str
    dados_antes: dict[str, Any] | None
    dados_depois: dict[str, Any] | None
    criado_em: datetime
