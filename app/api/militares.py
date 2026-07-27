"""Consulta de militares (aberta, sem login — regra 13.1)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.militar import Militar
from app.schemas.militar import MilitarDetalheOut, MilitarOut

router = APIRouter(prefix="/api/militares", tags=["militares"])


@router.get("", response_model=list[MilitarOut])
def listar_militares(apenas_ativos: bool = True, db: Session = Depends(get_db)):
    """Lista militares (por padrão só os ativos — regra 3.2)."""
    stmt = select(Militar).order_by(Militar.nome_guerra)
    if apenas_ativos:
        stmt = stmt.where(Militar.ativo.is_(True))
    return list(db.scalars(stmt))


@router.get("/{militar_id}", response_model=MilitarDetalheOut)
def obter_militar(militar_id: int, db: Session = Depends(get_db)):
    """Um militar com posto/graduação e OM aninhados."""
    militar = db.scalar(
        select(Militar)
        .where(Militar.id == militar_id)
        .options(joinedload(Militar.posto_graduacao), joinedload(Militar.om))
    )
    if militar is None:
        raise HTTPException(status_code=404, detail="militar não encontrado")
    return militar
