"""Consulta de escalas (aberta, sem login — regra 13.1)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models.escala import Escala
from app.schemas.escala import EscalaDetalheOut, EscalaOut
from app.services import escala_service

router = APIRouter(prefix="/api/escalas", tags=["escalas"])


@router.get("", response_model=list[EscalaOut])
def listar_escalas(apenas_ativas: bool = True, db: Session = Depends(get_db)):
    """Lista escalas (por padrão só as ativas — regra 4.4)."""
    stmt = select(Escala).order_by(Escala.nome)
    if apenas_ativas:
        stmt = stmt.where(Escala.ativa.is_(True))
    return list(db.scalars(stmt))


@router.get("/{escala_id}", response_model=EscalaDetalheOut)
def obter_escala(escala_id: int, db: Session = Depends(get_db)):
    """Uma escala com seus postos."""
    escala = db.scalar(
        select(Escala).where(Escala.id == escala_id).options(selectinload(Escala.postos))
    )
    if escala is None:
        raise HTTPException(status_code=404, detail="escala não encontrada")
    return escala


@router.get("/{escala_id}/concorrentes", response_model=list[int])
def concorrentes(escala_id: int, db: Session = Depends(get_db)):
    """Ids das escalas concorrentes desta (relação simétrica — regra 7.4.1)."""
    if db.get(Escala, escala_id) is None:
        raise HTTPException(status_code=404, detail="escala não encontrada")
    return escala_service.concorrentes_de(db, escala_id)
