"""Auditoria das alterações manuais (regra 11).

Toda mutação de gestão registra uma linha em `auditoria` com quem fez, o quê e
os estados antes/depois. `snapshot` fotografa um objeto ORM em dict serializável
em JSON; `registrar` acrescenta o registro na MESMA transação da mudança (a
camada de API dá o commit), de modo que auditoria e efeito são atômicos.
"""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.models.gestao import Auditoria

# Nunca gravar segredos na auditoria (ex.: hash de senha do usuário).
_EXCLUIR_PADRAO = frozenset({"senha_hash"})


def _json_safe(valor: Any) -> Any:
    if isinstance(valor, (date, datetime, time)):  # time não é subclasse de date
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, Enum):
        return valor.value
    return valor


def snapshot(obj: Any, *, excluir: frozenset[str] = _EXCLUIR_PADRAO) -> dict[str, Any]:
    """Fotografa as colunas do objeto ORM num dict pronto para JSON."""
    mapper = inspect(obj).mapper
    return {
        attr.key: _json_safe(getattr(obj, attr.key))
        for attr in mapper.column_attrs
        if attr.key not in excluir
    }


def diferencas(
    antes: dict[str, Any] | None, depois: dict[str, Any] | None,
) -> list[tuple[str, Any, Any]]:
    """O que de fato mudou entre dois retratos: [(campo, antes, depois)].

    É o que a tela de auditoria mostra (regra 11). Sem isso o gestor teria de
    comparar a olho dois JSON de 12 campos para achar o único que mudou.
    Só faz sentido em 'alterar' — em criar/excluir um dos lados é None e a tela
    mostra o retrato inteiro.
    """
    if not antes or not depois:
        return []
    campos = list(dict.fromkeys([*antes, *depois]))   # ordem estável do 'antes'
    return [(campo, antes.get(campo), depois.get(campo))
            for campo in campos if antes.get(campo) != depois.get(campo)]


def registrar(
    db: Session,
    *,
    usuario_id: int | None,
    entidade: str,
    acao: str,                      # 'criar' | 'alterar' | 'excluir'
    entidade_id: int | None = None,
    antes: dict[str, Any] | None = None,
    depois: dict[str, Any] | None = None,
) -> Auditoria:
    """Enfileira o registro de auditoria (sem commit — quem chama commita)."""
    reg = Auditoria(
        usuario_id=usuario_id,
        entidade=entidade,
        entidade_id=entidade_id,
        acao=acao,
        dados_antes=antes,
        dados_depois=depois,
    )
    db.add(reg)
    return reg
