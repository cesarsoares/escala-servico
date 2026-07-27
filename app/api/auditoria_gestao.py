"""Leitura da auditoria (regra 11) — protegida por login.

O histórico de todas as alterações manuais, só para gestores. É consulta (não
muta nada e não se auto-audita). Filtros por entidade/registro/gestor e janela
temporal, com paginação; do mais recente ao mais antigo.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.gestao import Auditoria, Usuario
from app.schemas.gestao import AuditoriaOut

router = APIRouter(prefix="/api/auditoria", tags=["gestão: auditoria"])


def _naive_utc(dt: datetime | None) -> datetime | None:
    """Normaliza um datetime tz-aware para naive-UTC.

    `Auditoria.criado_em` é DateTime SEM timezone (func.now() grava em UTC). Um
    filtro tz-aware compararia errado (no SQLite, contra strings ISO com offset);
    convertemos para UTC e removemos o tzinfo para casar com a coluna.
    """
    if dt is not None and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


@router.get("", response_model=list[AuditoriaOut])
def listar_auditoria(
    entidade: str | None = Query(None, description="ex.: 'militar', 'escala', 'permuta'"),
    entidade_id: int | None = Query(None, description="id do registro auditado"),
    usuario_id: int | None = Query(None, description="gestor que fez a alteração"),
    desde: datetime | None = Query(None, description="a partir de (inclusive)"),
    ate: datetime | None = Query(None, description="até (inclusive)"),
    limite: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Lista registros de auditoria (regra 11), do mais recente ao mais antigo."""
    stmt = select(Auditoria).order_by(Auditoria.criado_em.desc(), Auditoria.id.desc())
    if entidade is not None:
        stmt = stmt.where(Auditoria.entidade == entidade)
    if entidade_id is not None:
        stmt = stmt.where(Auditoria.entidade_id == entidade_id)
    if usuario_id is not None:
        stmt = stmt.where(Auditoria.usuario_id == usuario_id)
    desde = _naive_utc(desde)
    ate = _naive_utc(ate)
    if desde is not None:
        stmt = stmt.where(Auditoria.criado_em >= desde)
    if ate is not None:
        stmt = stmt.where(Auditoria.criado_em <= ate)
    stmt = stmt.limit(limite).offset(offset)
    return list(db.scalars(stmt))
