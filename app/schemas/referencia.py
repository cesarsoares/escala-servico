"""Schemas das tabelas de referência.

organizacao_militar e tipo_impedimento são gerenciados pelo gestor (CRUD).
circulo_hierarquico e posto_graduacao são fixos por lei (Lei 6.880/80, art. 16)
e vêm do seed — expostos apenas para leitura.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.base import Entrada, Resposta


# --- Organização Militar (regra 3.2) ---
class OrganizacaoMilitarCreate(Entrada):
    nome: str = Field(max_length=120)
    sigla: str = Field(max_length=30)


class OrganizacaoMilitarUpdate(Entrada):
    nome: str | None = Field(default=None, max_length=120)
    sigla: str | None = Field(default=None, max_length=30)


class OrganizacaoMilitarOut(Resposta):
    id: int
    nome: str
    sigla: str


# --- Círculo hierárquico (art. 16; fixo por lei) ---
class CirculoHierarquicoOut(Resposta):
    id: int
    nome: str
    ordem: int      # maior = mais antigo
    eh_praca: bool  # regra 9.5


# --- Posto/graduação (art. 16; fixo por lei) ---
class PostoGraduacaoOut(Resposta):
    id: int
    sigla: str
    nome: str
    ordem_hierarquica: int
    circulo_id: int


class PostoGraduacaoDetalheOut(PostoGraduacaoOut):
    """Posto/graduação com o círculo aninhado (para telas de cadastro)."""
    circulo: CirculoHierarquicoOut


# --- Tipo de impedimento (regra 7.5) ---
class TipoImpedimentoCreate(Entrada):
    nome: str = Field(max_length=60)


class TipoImpedimentoUpdate(Entrada):
    nome: str | None = Field(default=None, max_length=60)


class TipoImpedimentoOut(Resposta):
    id: int
    nome: str
