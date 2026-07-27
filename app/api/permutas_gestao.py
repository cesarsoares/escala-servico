"""Gestão de permutas (regra 9) — protegida por login (regra 11).

Registro PURO: a permuta anota que outro militar cobriu um serviço; a folga NÃO
muda de dono (segue em servico.militar_id). A guarda de folga mínima do
substituto (regra 10.5) mora no service `permuta`. Toda mutação é auditada; o
`autorizado_por` vem do gestor logado, não do corpo.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.gestao import Usuario
from app.models.servico import Permuta
from app.schemas.servico import PermutaCreate, PermutaOut
from app.services import auditoria, permuta as permuta_service

router = APIRouter(prefix="/api/permutas", tags=["gestão: permutas"])


@router.post("", response_model=PermutaOut, status_code=status.HTTP_201_CREATED)
def registrar_permuta(
    dados: PermutaCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Registra a cobertura de um serviço por um substituto (regra 9).

    404 se serviço/militar não existem; 409 se a troca fere uma regra de guarda
    (substituto = escalado, serviço já permutado, folga mínima ou impedimento —
    regra 10.5). A folga NÃO é recalculada.
    """
    try:
        permuta = permuta_service.registrar_permuta(
            db,
            servico_id=dados.servico_id,
            militar_substituto_id=dados.militar_substituto_id,
            autorizado_por=usuario.id,
            observacao=dados.observacao,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except permuta_service.PermutaNegada as e:
        raise HTTPException(status_code=409, detail=e.motivo)

    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="permuta", entidade_id=permuta.id,
        acao="criar", depois=auditoria.snapshot(permuta),
    )
    db.commit()
    return permuta


@router.delete("/{servico_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancelar_permuta(
    servico_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Cancela a permuta de um serviço (o escalado volta a figurar). 404 se não há."""
    permuta = db.scalar(select(Permuta).where(Permuta.servico_id == servico_id))
    if permuta is None:
        raise HTTPException(status_code=404, detail="permuta não encontrada")

    antes = auditoria.snapshot(permuta)
    permuta_id = permuta.id
    permuta_service.cancelar_permuta(db, servico_id)
    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="permuta", entidade_id=permuta_id,
        acao="excluir", antes=antes,
    )
    db.commit()
