"""Schemas do militar (regras 3.2, 9.x; Lei 6.880/80, art. 16-17).

Regra que NÃO cabe aqui: "numero_antiguidade só para praças" depende do círculo
do posto_graduacao (eh_praca) — é validação cross-tabela, feita na camada de
serviço, não no schema.
"""
from __future__ import annotations

from datetime import date

from pydantic import Field, field_validator

from app.normalizacao import so_digitos
from app.schemas.base import Entrada, Resposta
from app.schemas.referencia import OrganizacaoMilitarOut, PostoGraduacaoOut


def _normaliza_documento(v: str | None) -> str | None:
    """Guarda CPF/identidade só em dígitos, como a ficha em PDF faz.

    Sem isso, '605.126.360-87' e '60512636087' são valores distintos: escapam do
    UNIQUE e criam dois cadastros da mesma pessoa concorrendo na fila.
    """
    if not isinstance(v, str):
        return v          # None ou tipo errado: deixa o pydantic decidir
    digitos = so_digitos(v)
    if digitos is None:
        raise ValueError("deve conter dígitos")
    return digitos


class MilitarBase(Entrada):
    nome_guerra: str = Field(max_length=60)
    nome_completo: str = Field(max_length=160)
    # Núcleo obrigatório = nome + posto + OM. Identidade e datas de antiguidade
    # são OPCIONAIS: entram pela planilha vazias e são completadas pela ficha.
    identidade: str | None = Field(default=None, max_length=20, min_length=1)  # varchar: preserva zero à esquerda
    cpf: str | None = Field(default=None, max_length=14, min_length=1)
    posto_graduacao_id: int
    om_id: int
    data_promocao: date | None = None         # regra 9.2
    data_praca: date | None = None            # regra 9.3
    data_nascimento: date | None = None       # art. 17
    numero_antiguidade: int | None = Field(default=None, ge=1)  # regra 9.5 (só praças)
    ordem_manual: int = Field(default=0, ge=0)  # regra 9.4
    ativo: bool = True

    @field_validator("identidade", "cpf", mode="before")
    @classmethod
    def _documentos_em_digitos(cls, v):
        return _normaliza_documento(v)


class MilitarCreate(MilitarBase):
    pass


class MilitarUpdate(Entrada):
    """PATCH: todos opcionais; só os campos enviados são alterados."""
    nome_guerra: str | None = Field(default=None, max_length=60)
    nome_completo: str | None = Field(default=None, max_length=160)
    identidade: str | None = Field(default=None, max_length=20, min_length=1)
    cpf: str | None = Field(default=None, max_length=14, min_length=1)
    posto_graduacao_id: int | None = None
    om_id: int | None = None
    data_promocao: date | None = None
    data_praca: date | None = None
    data_nascimento: date | None = None
    numero_antiguidade: int | None = Field(default=None, ge=1)
    ordem_manual: int | None = Field(default=None, ge=0)
    ativo: bool | None = None

    @field_validator("identidade", "cpf", mode="before")
    @classmethod
    def _documentos_em_digitos(cls, v):
        return _normaliza_documento(v)


class MilitarResumoOut(Resposta):
    """Militar enxuto, para aninhar em serviço/permuta (evita payload gigante)."""
    id: int
    nome_guerra: str
    posto_graduacao_id: int


class MilitarOut(Resposta):
    id: int
    nome_guerra: str
    nome_completo: str
    identidade: str | None
    cpf: str | None
    posto_graduacao_id: int
    om_id: int
    data_promocao: date | None
    data_praca: date | None
    data_nascimento: date | None
    numero_antiguidade: int | None
    ordem_manual: int
    ativo: bool


class MilitarDetalheOut(MilitarOut):
    """Militar com posto/graduação e OM aninhados (telas de cadastro/consulta)."""
    posto_graduacao: PostoGraduacaoOut
    om: OrganizacaoMilitarOut


class RascunhoMilitar(Resposta):
    """Militar lido de uma ficha, ainda NÃO gravado.

    Tudo é opcional (inclusive posto/OM, que no MilitarCreate são obrigatórios):
    o que a ficha não trouxer ou o que não reconciliar com as tabelas de
    referência fica em branco para o operador completar.
    """
    nome_guerra: str | None = None
    nome_completo: str | None = None
    identidade: str | None = None
    cpf: str | None = None
    posto_graduacao_id: int | None = None
    om_id: int | None = None
    data_promocao: date | None = None
    data_praca: date | None = None
    data_nascimento: date | None = None
    numero_antiguidade: int | None = None   # nunca vem da ficha (regra 9.5)


class FichaImportadaOut(Resposta):
    """Rascunho + avisos. Não grava nada: alimenta o formulário de cadastro."""
    formato: str
    militar: RascunhoMilitar
    avisos: list[str]
