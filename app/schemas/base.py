"""Bases compartilhadas dos schemas Pydantic.

Duas famílias:
  - `Resposta`: schemas de saída (leem direto do objeto ORM via from_attributes).
  - `Entrada`: schemas de entrada (normalizam espaços em branco).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Resposta(BaseModel):
    """Base dos schemas de resposta: monta a partir do atributo ORM."""
    model_config = ConfigDict(from_attributes=True)


class Entrada(BaseModel):
    """Base dos schemas de entrada: apara espaços e ignora extras silenciosos."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
