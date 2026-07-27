"""Gestão do calendário (seção 5) — protegida por login (regra 11).

Feriados (regra 5.2: nacionais embutidos + adicionados pelo gestor) e overrides
de cor de um dia (regra 5.3: gestor força a cor de um dia específico). São dados
de referência, sem histórico apontando para eles — a exclusão é física. Toda
mutação é auditada.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import extract, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.calendario import Feriado, OverrideDia
from app.models.gestao import Usuario
from app.schemas.calendario import (
    FeriadoCreate,
    FeriadoOut,
    FeriadoUpdate,
    OverrideDiaOut,
    OverrideDiaUpsert,
)
from app.services import auditoria

router = APIRouter(prefix="/api/feriados", tags=["gestão: calendário"])
router_overrides = APIRouter(prefix="/api/overrides", tags=["gestão: calendário"])


# --- Feriados (regra 5.2) ---
@router.get("", response_model=list[FeriadoOut])
def listar_feriados(
    ano: int | None = Query(None, description="filtra por ano"),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Lista feriados (opcionalmente de um ano), ordenados por data."""
    stmt = select(Feriado).order_by(Feriado.data)
    if ano is not None:
        stmt = stmt.where(extract("year", Feriado.data) == ano)
    return list(db.scalars(stmt))


@router.post("", response_model=FeriadoOut, status_code=status.HTTP_201_CREATED)
def criar_feriado(
    dados: FeriadoCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Adiciona um feriado (regra 5.2). Data é única."""
    feriado = Feriado(**dados.model_dump())
    db.add(feriado)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="já existe feriado nesta data")

    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="feriado", entidade_id=feriado.id,
        acao="criar", depois=auditoria.snapshot(feriado),
    )
    db.commit()
    return feriado


@router.patch("/{feriado_id}", response_model=FeriadoOut)
def alterar_feriado(
    feriado_id: int,
    dados: FeriadoUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Altera nome/marcação nacional do feriado (a data é fixa)."""
    feriado = db.get(Feriado, feriado_id)
    if feriado is None:
        raise HTTPException(status_code=404, detail="feriado não encontrado")

    campos = dados.model_dump(exclude_unset=True)
    if not campos:
        return feriado
    antes = auditoria.snapshot(feriado)
    for campo, valor in campos.items():
        setattr(feriado, campo, valor)
    db.flush()
    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="feriado", entidade_id=feriado.id,
        acao="alterar", antes=antes, depois=auditoria.snapshot(feriado),
    )
    db.commit()
    return feriado


@router.delete("/{feriado_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_feriado(
    feriado_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Remove um feriado (físico). Não recalcula escalas já gravadas."""
    feriado = db.get(Feriado, feriado_id)
    if feriado is None:
        raise HTTPException(status_code=404, detail="feriado não encontrado")
    antes = auditoria.snapshot(feriado)
    db.delete(feriado)
    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="feriado", entidade_id=feriado_id,
        acao="excluir", antes=antes,
    )
    db.commit()


# --- Override de cor de um dia (regra 5.3) ---
@router_overrides.get("", response_model=list[OverrideDiaOut])
def listar_overrides(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Lista os overrides de cor, ordenados por data."""
    return list(db.scalars(select(OverrideDia).order_by(OverrideDia.data)))


@router_overrides.put("/{dia}", response_model=OverrideDiaOut)
def definir_override(
    dia: date,
    dados: OverrideDiaUpsert,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Define (ou redefine) a cor forçada de um dia (regra 5.3). Upsert pela data."""
    if dados.data != dia:
        raise HTTPException(status_code=422, detail="data do corpo difere da URL")

    override = db.get(OverrideDia, dia)
    if override is None:
        override = OverrideDia(data=dia, cor=dados.cor, observacao=dados.observacao)
        db.add(override)
        db.flush()
        auditoria.registrar(
            db, usuario_id=usuario.id, entidade="override_dia",
            acao="criar", depois=auditoria.snapshot(override),
        )
    else:
        antes = auditoria.snapshot(override)
        override.cor = dados.cor
        override.observacao = dados.observacao
        db.flush()
        auditoria.registrar(
            db, usuario_id=usuario.id, entidade="override_dia",
            acao="alterar", antes=antes, depois=auditoria.snapshot(override),
        )
    db.commit()
    return override


@router_overrides.delete("/{dia}", status_code=status.HTTP_204_NO_CONTENT)
def remover_override(
    dia: date,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Remove o override de um dia (o dia volta à cor natural — regra 5.1)."""
    override = db.get(OverrideDia, dia)
    if override is None:
        raise HTTPException(status_code=404, detail="override não encontrado")
    antes = auditoria.snapshot(override)
    db.delete(override)
    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="override_dia",
        acao="excluir", antes=antes,
    )
    db.commit()
