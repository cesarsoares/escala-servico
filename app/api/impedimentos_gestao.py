"""Gestão de impedimentos (regra 7.5) — protegida por login (regra 11).

Dispensa, férias, curso, operação: o militar é pulado no período mas mantém a
vez (a fila não muda de ordem). Registro de evento, sem histórico apontando para
ele — a exclusão é física. Toda mutação é auditada.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.database import get_db
from app.models.gestao import Usuario
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import TipoImpedimento
from app.schemas.impedimento import (
    ImpedimentoCreate,
    ImpedimentoDetalheOut,
    ImpedimentoOut,
    ImpedimentoUpdate,
)
from app.services import auditoria

router = APIRouter(prefix="/api/impedimentos", tags=["gestão: impedimentos"])


def _validar_fks(db: Session, *, militar_id: int | None, tipo_impedimento_id: int | None) -> None:
    if militar_id is not None and db.get(Militar, militar_id) is None:
        raise HTTPException(status_code=422, detail="militar_id inexistente")
    if tipo_impedimento_id is not None and db.get(TipoImpedimento, tipo_impedimento_id) is None:
        raise HTTPException(status_code=422, detail="tipo_impedimento_id inexistente")


@router.get("", response_model=list[ImpedimentoDetalheOut])
def listar_impedimentos(
    militar_id: int | None = Query(None, description="filtra por militar"),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Lista impedimentos (opcionalmente de um militar), do mais recente ao mais antigo."""
    stmt = (
        select(Impedimento)
        .options(joinedload(Impedimento.tipo))
        .order_by(Impedimento.inicio.desc())
    )
    if militar_id is not None:
        stmt = stmt.where(Impedimento.militar_id == militar_id)
    return list(db.scalars(stmt))


@router.post("", response_model=ImpedimentoOut, status_code=status.HTTP_201_CREATED)
def criar_impedimento(
    dados: ImpedimentoCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Registra um impedimento (regra 7.5). fim >= inicio já validado no schema."""
    _validar_fks(db, militar_id=dados.militar_id, tipo_impedimento_id=dados.tipo_impedimento_id)
    imp = Impedimento(**dados.model_dump())
    db.add(imp)
    db.flush()
    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="impedimento", entidade_id=imp.id,
        acao="criar", depois=auditoria.snapshot(imp),
    )
    db.commit()
    return imp


@router.patch("/{impedimento_id}", response_model=ImpedimentoOut)
def alterar_impedimento(
    impedimento_id: int,
    dados: ImpedimentoUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Altera tipo/período/observação. Revalida fim >= inicio no estado resultante."""
    imp = db.get(Impedimento, impedimento_id)
    if imp is None:
        raise HTTPException(status_code=404, detail="impedimento não encontrado")

    campos = dados.model_dump(exclude_unset=True)
    if not campos:
        return imp

    _validar_fks(db, militar_id=None, tipo_impedimento_id=campos.get("tipo_impedimento_id"))
    inicio = campos.get("inicio", imp.inicio)
    fim = campos.get("fim", imp.fim)
    if fim < inicio:
        raise HTTPException(status_code=422, detail="'fim' não pode ser anterior a 'inicio'")

    antes = auditoria.snapshot(imp)
    for campo, valor in campos.items():
        setattr(imp, campo, valor)
    db.flush()
    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="impedimento", entidade_id=imp.id,
        acao="alterar", antes=antes, depois=auditoria.snapshot(imp),
    )
    db.commit()
    return imp


@router.delete("/{impedimento_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_impedimento(
    impedimento_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Remove um impedimento (físico). Não recalcula escalas já gravadas."""
    imp = db.get(Impedimento, impedimento_id)
    if imp is None:
        raise HTTPException(status_code=404, detail="impedimento não encontrado")
    antes = auditoria.snapshot(imp)
    db.delete(imp)
    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="impedimento", entidade_id=impedimento_id,
        acao="excluir", antes=antes,
    )
    db.commit()
