"""Gestão de militares (regra 3.2) — protegida por login (regra 11).

A CONSULTA (GET) fica em `militares.py`, aberta (regra 13.1). Aqui ficam as
mutações: criar, alterar e excluir (exclusão LÓGICA — desativa, preservando o
histórico de serviço/permutas ligado ao militar). Toda mutação é auditada.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.database import get_db
from app.models.gestao import Usuario
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.schemas.militar import (
    FichaImportadaOut, MilitarCreate, MilitarOut, MilitarUpdate, RascunhoMilitar,
)
from app.services import auditoria, importacao
from app.services.ficha import FichaInvalida, extrair

router = APIRouter(prefix="/api/militares", tags=["gestão: militares"])

MAX_FICHA_BYTES = 20 * 1024 * 1024   # as fichas do SCGPE passam de 1 MB com as fotos


def _validar_fks_e_antiguidade(
    db: Session,
    *,
    posto_graduacao_id: int | None,
    om_id: int | None,
    numero_antiguidade: int | None,
) -> None:
    """Valida FKs existentes e a regra 9.5 (nº de antiguidade só para praças).

    Só checa antiguidade quando o posto/graduação é conhecido (create sempre;
    update só se veio posto_graduacao_id). O vínculo praça↔antiguidade é
    cross-tabela (via circulo.eh_praca), por isso mora aqui e não no schema.
    """
    if om_id is not None and db.get(OrganizacaoMilitar, om_id) is None:
        raise HTTPException(status_code=422, detail="om_id inexistente")

    if posto_graduacao_id is not None:
        pg = db.scalar(
            select(PostoGraduacao)
            .options(joinedload(PostoGraduacao.circulo))
            .where(PostoGraduacao.id == posto_graduacao_id)
        )
        if pg is None:
            raise HTTPException(status_code=422, detail="posto_graduacao_id inexistente")
        if numero_antiguidade is not None and not pg.circulo.eh_praca:
            raise HTTPException(
                status_code=422,
                detail="numero_antiguidade só se aplica a praças (regra 9.5)",
            )


def ler_ficha(conteudo: bytes, db: Session, militar_id: int | None = None) -> FichaImportadaOut:
    """Extrai + reconcilia uma ficha em PDF. Levanta HTTPException 422/413.

    `militar_id` = militar sendo editado; não é acusado de duplicata de si mesmo.
    """
    if not conteudo:
        raise HTTPException(status_code=422, detail="envie um arquivo PDF")
    if len(conteudo) > MAX_FICHA_BYTES:
        raise HTTPException(status_code=413, detail="arquivo maior que 20 MB")
    try:
        ficha = extrair(conteudo)
    except FichaInvalida as e:
        raise HTTPException(status_code=422, detail=str(e))
    valores, avisos = importacao.rascunho(db, ficha, militar_id)
    return FichaImportadaOut(
        formato=ficha.formato, militar=RascunhoMilitar(**valores), avisos=avisos,
    )


@router.post("/importar-ficha", response_model=FichaImportadaOut)
def importar_ficha(
    arquivo: UploadFile = File(...),
    militar_id: int | None = Query(None, description="militar sendo editado (não é duplicata de si mesmo)"),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Lê a ficha individual (PDF) e devolve um RASCUNHO — não grava nada.

    Suporta os dois formatos (SiCaPEx e SCGPE). O cadastro continua sendo o
    POST normal, depois que o operador conferir os dados (regra 3.2). Por não
    alterar nada, não gera auditoria.
    """
    return ler_ficha(arquivo.file.read(MAX_FICHA_BYTES + 1), db, militar_id)


@router.post("", response_model=MilitarOut, status_code=status.HTTP_201_CREATED)
def criar_militar(
    dados: MilitarCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Cadastra um militar (regra 3.2)."""
    _validar_fks_e_antiguidade(
        db,
        posto_graduacao_id=dados.posto_graduacao_id,
        om_id=dados.om_id,
        numero_antiguidade=dados.numero_antiguidade,
    )
    militar = Militar(**dados.model_dump())
    db.add(militar)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="identidade ou CPF já cadastrado")

    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="militar", entidade_id=militar.id,
        acao="criar", depois=auditoria.snapshot(militar),
    )
    db.commit()
    return militar


@router.patch("/{militar_id}", response_model=MilitarOut)
def alterar_militar(
    militar_id: int,
    dados: MilitarUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Altera campos de um militar (só os enviados)."""
    militar = db.get(Militar, militar_id)
    if militar is None:
        raise HTTPException(status_code=404, detail="militar não encontrado")

    campos = dados.model_dump(exclude_unset=True)
    if not campos:
        return militar  # nada a fazer; não gera auditoria

    antes = auditoria.snapshot(militar)
    # Revalida FKs/antiguidade sobre o ESTADO RESULTANTE do merge: usa o posto
    # atual quando o PATCH não o troca, senão setar numero_antiguidade num oficial
    # (sem enviar posto) escaparia da checagem 9.5 (posto cairia em None).
    _validar_fks_e_antiguidade(
        db,
        posto_graduacao_id=campos.get("posto_graduacao_id", militar.posto_graduacao_id),
        om_id=campos.get("om_id"),
        numero_antiguidade=campos.get("numero_antiguidade", militar.numero_antiguidade),
    )
    for campo, valor in campos.items():
        setattr(militar, campo, valor)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="identidade ou CPF já cadastrado")

    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="militar", entidade_id=militar.id,
        acao="alterar", antes=antes, depois=auditoria.snapshot(militar),
    )
    db.commit()
    return militar


@router.delete("/{militar_id}", response_model=MilitarOut)
def excluir_militar(
    militar_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Exclusão LÓGICA: desativa o militar (ativo=False), preservando histórico.

    Reativação é via PATCH ativo=true. Militar inativo some da consulta padrão
    (apenas_ativos=True) e não entra em novas rotações.
    """
    militar = db.get(Militar, militar_id)
    if militar is None:
        raise HTTPException(status_code=404, detail="militar não encontrado")

    antes = auditoria.snapshot(militar)
    militar.ativo = False
    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="militar", entidade_id=militar.id,
        acao="excluir", antes=antes, depois=auditoria.snapshot(militar),
    )
    db.commit()
    return militar
