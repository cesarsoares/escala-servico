"""Consulta da escala fechada — quem serve em cada dia (aberta — regra 13.1).

Lê os `servico` já gravados pelo motor (o documento publicado). Não recalcula:
a previsão dinâmica é assunto da camada de gestão.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.servico import Servico
from app.schemas.servico import ServicoDetalheOut

router = APIRouter(prefix="/api/servicos", tags=["servicos"])

MAX_DIAS = 366  # trava simples contra varredura acidental de anos


@router.get("", response_model=list[ServicoDetalheOut])
def listar_servicos(
    inicio: date = Query(..., description="primeiro dia (inclusive)"),
    fim: date = Query(..., description="último dia (inclusive)"),
    escala_id: int | None = Query(None, description="filtra por escala"),
    db: Session = Depends(get_db),
):
    """Serviços gravados no período, com o militar escalado aninhado.

    Ordenados por dia e posto. Janela limitada a ~1 ano por chamada.
    """
    if fim < inicio:
        raise HTTPException(status_code=422, detail="'fim' anterior a 'inicio'")
    if (fim - inicio).days > MAX_DIAS:
        raise HTTPException(status_code=422, detail=f"período máximo de {MAX_DIAS} dias")

    stmt = (
        select(Servico)
        .where(Servico.dia >= inicio, Servico.dia <= fim)
        .options(joinedload(Servico.militar))
        .order_by(Servico.dia, Servico.posto_id)
    )
    if escala_id is not None:
        stmt = stmt.where(Servico.escala_id == escala_id)
    return list(db.scalars(stmt))
