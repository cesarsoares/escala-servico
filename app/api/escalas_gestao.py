"""Gestão de escalas (seção 4) — protegida por login (regra 11).

A CONSULTA (GET) fica em `escalas.py`, aberta (regra 13.1). Aqui: criar a escala
com seus postos, alterar, extinguir (exclusão LÓGICA — regra 8, preservando o
histórico de serviço), gerir participantes (regra 3.3/7.6) e declarar/remover
concorrência (regra 7.4.1). Toda mutação é auditada.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user
from app.database import get_db
from app.models.escala import Escala, EscalaConcorrente, Participacao, Posto
from app.models.gestao import Usuario
from app.models.militar import Militar
from app.schemas.escala import (
    ConcorrenteIn,
    EscalaConcorrenteOut,
    EscalaCreate,
    EscalaDetalheOut,
    EscalaOut,
    EscalaUpdate,
    ParticipacaoOut,
    ParticipanteIn,
)
from app.schemas.servico import EscalacaoDiaOut, EscalacaoOut, EscalacaoPedido
from app.services import auditoria, escala_service, rotacao

# trava simples contra fechar anos por engano (espelha a consulta de serviços)
MAX_DIAS_ESCALACAO = 366

router = APIRouter(prefix="/api/escalas", tags=["gestão: escalas"])


def _obter_escala(db: Session, escala_id: int) -> Escala:
    escala = db.get(Escala, escala_id)
    if escala is None:
        raise HTTPException(status_code=404, detail="escala não encontrada")
    return escala


# --- Escala ---
@router.post("", response_model=EscalaDetalheOut, status_code=status.HTTP_201_CREATED)
def criar_escala(
    dados: EscalaCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Cria a escala com seus postos (>=1 posto — regra 2.5)."""
    campos = dados.model_dump(exclude={"postos"})
    escala = Escala(**campos)
    escala.postos = [Posto(ordem=p.ordem, rotulo=p.rotulo) for p in dados.postos]
    db.add(escala)
    db.flush()

    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="escala", entidade_id=escala.id,
        acao="criar", depois=auditoria.snapshot(escala),
    )
    db.commit()
    # recarrega com os postos para o EscalaDetalheOut
    return db.scalar(
        select(Escala).where(Escala.id == escala.id).options(selectinload(Escala.postos))
    )


@router.patch("/{escala_id}", response_model=EscalaOut)
def alterar_escala(
    escala_id: int,
    dados: EscalaUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Altera atributos da escala (não os postos). Não pode zerar as duas cores."""
    escala = _obter_escala(db, escala_id)
    campos = dados.model_dump(exclude_unset=True)
    if not campos:
        return escala

    # Estado resultante não pode ficar sem nenhuma cor (regra 4.5 / ck_ao_menos_uma_cor).
    tem_preta = campos.get("tem_preta", escala.tem_preta)
    tem_vermelha = campos.get("tem_vermelha", escala.tem_vermelha)
    if not tem_preta and not tem_vermelha:
        raise HTTPException(
            status_code=422,
            detail="a escala precisa rodar ao menos uma cor (preta ou vermelha)",
        )

    antes = auditoria.snapshot(escala)
    for campo, valor in campos.items():
        setattr(escala, campo, valor)
    db.flush()
    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="escala", entidade_id=escala.id,
        acao="alterar", antes=antes, depois=auditoria.snapshot(escala),
    )
    db.commit()
    return escala


@router.delete("/{escala_id}", response_model=EscalaOut)
def extinguir_escala(
    escala_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Extinção LÓGICA da escala (ativa=False — regra 8), preservando histórico.

    Reativação via PATCH ativa=true. Escala inativa some da consulta padrão e não
    entra em novas escalações.
    """
    escala = _obter_escala(db, escala_id)
    antes = auditoria.snapshot(escala)
    escala.ativa = False
    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="escala", entidade_id=escala.id,
        acao="excluir", antes=antes, depois=auditoria.snapshot(escala),
    )
    db.commit()
    return escala


# --- Escalação de período: dispara o motor e grava os serviços (regras 6/7) ---
@router.post("/{escala_id}/escalar", response_model=EscalacaoOut)
def escalar_periodo(
    escala_id: int,
    pedido: EscalacaoPedido,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Fecha a escala de `inicio` a `fim` (inclusive), gravando os serviços.

    Cronológico: cada dia alimenta a fila/folga do seguinte (regra 6/7). Retorna
    o resumo diário e a contagem de dias com efetivo insuficiente (regra 7.8).
    Idempotente: dias já gravados não são duplicados.
    """
    if pedido.fim < pedido.inicio:
        raise HTTPException(status_code=422, detail="'fim' anterior a 'inicio'")
    # intervalo é INCLUSIVE: nº de dias = diff + 1 (senão o limite efetivo seria 367)
    if (pedido.fim - pedido.inicio).days + 1 > MAX_DIAS_ESCALACAO:
        raise HTTPException(
            status_code=422, detail=f"período máximo de {MAX_DIAS_ESCALACAO} dias"
        )
    _obter_escala(db, escala_id)

    try:
        resultados = rotacao.escalar_e_gravar_periodo(db, escala_id, pedido.inicio, pedido.fim)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    dias = [
        EscalacaoDiaOut(
            dia=r.dia,
            cor=r.cor,
            militar_ids=[m.id for m in r.escolhidos],
            postos_solicitados=r.postos_solicitados,
            efetivo_insuficiente=r.efetivo_insuficiente,
        )
        for r in resultados
    ]
    dias_com_alerta = sum(1 for d in dias if d.efetivo_insuficiente)

    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="escala", entidade_id=escala_id,
        acao="escalar",
        depois={
            "inicio": pedido.inicio.isoformat(),
            "fim": pedido.fim.isoformat(),
            "dias": len(dias),
            "servicos_intencionados": sum(len(d.militar_ids) for d in dias),
            "dias_com_alerta": dias_com_alerta,
        },
    )
    db.commit()
    return EscalacaoOut(
        escala_id=escala_id,
        inicio=pedido.inicio,
        fim=pedido.fim,
        dias=dias,
        dias_com_alerta=dias_com_alerta,
    )


# --- Participação (regra 3.3; isenção permanente = não-participação, regra 7.6) ---
@router.post(
    "/{escala_id}/participacoes",
    response_model=ParticipacaoOut,
    status_code=status.HTTP_201_CREATED,
)
def adicionar_participante(
    escala_id: int,
    dados: ParticipanteIn,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Vincula um militar à escala. Se já existir inativo, reativa (isenção desfeita)."""
    _obter_escala(db, escala_id)
    if db.get(Militar, dados.militar_id) is None:
        raise HTTPException(status_code=422, detail="militar_id inexistente")

    part = db.scalar(
        select(Participacao).where(
            Participacao.escala_id == escala_id,
            Participacao.militar_id == dados.militar_id,
        )
    )
    if part is not None:
        if part.ativo:
            raise HTTPException(status_code=409, detail="militar já participa da escala")
        antes = auditoria.snapshot(part)
        part.ativo = True
        db.flush()
        auditoria.registrar(
            db, usuario_id=usuario.id, entidade="participacao", entidade_id=part.id,
            acao="alterar", antes=antes, depois=auditoria.snapshot(part),
        )
        db.commit()
        return part

    part = Participacao(escala_id=escala_id, militar_id=dados.militar_id, ativo=True)
    db.add(part)
    db.flush()
    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="participacao", entidade_id=part.id,
        acao="criar", depois=auditoria.snapshot(part),
    )
    db.commit()
    return part


@router.delete("/{escala_id}/participacoes/{militar_id}", response_model=ParticipacaoOut)
def remover_participante(
    escala_id: int,
    militar_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Isenção permanente (regra 7.6): desativa a participação, sem apagar o vínculo."""
    part = db.scalar(
        select(Participacao).where(
            Participacao.escala_id == escala_id,
            Participacao.militar_id == militar_id,
        )
    )
    if part is None:
        raise HTTPException(status_code=404, detail="participação não encontrada")

    antes = auditoria.snapshot(part)
    part.ativo = False
    auditoria.registrar(
        db, usuario_id=usuario.id, entidade="participacao", entidade_id=part.id,
        acao="excluir", antes=antes, depois=auditoria.snapshot(part),
    )
    db.commit()
    return part


# --- Concorrência (relação explícita e simétrica — regra 7.4.1) ---
@router.post(
    "/{escala_id}/concorrentes",
    response_model=EscalaConcorrenteOut,
    status_code=status.HTTP_201_CREATED,
)
def declarar_concorrente(
    escala_id: int,
    dados: ConcorrenteIn,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Declara a escala do path e `escala_b_id` como concorrentes (regra 7.4.1)."""
    # o service é idempotente (devolve o par existente sem inserir); só auditamos
    # quando o par é REALMENTE novo, para não poluir a auditoria com 'criar' falsos
    ja_existia = db.get(EscalaConcorrente, tuple(sorted((escala_id, dados.escala_b_id)))) is not None
    try:
        par = escala_service.declarar_concorrencia(db, escala_id, dados.escala_b_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if not ja_existia:
        auditoria.registrar(
            db, usuario_id=usuario.id, entidade="escala_concorrente", entidade_id=escala_id,
            acao="criar", depois=auditoria.snapshot(par),
        )
    db.commit()
    return par


@router.delete("/{escala_id}/concorrentes/{outra_id}", status_code=status.HTTP_204_NO_CONTENT)
def remover_concorrente(
    escala_id: int,
    outra_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    """Desfaz a concorrência entre as duas escalas (idempotente — regra 7.4.1)."""
    removeu = escala_service.remover_concorrencia(db, escala_id, outra_id)
    if removeu:
        menor, maior = sorted((escala_id, outra_id))
        auditoria.registrar(
            db, usuario_id=usuario.id, entidade="escala_concorrente", entidade_id=escala_id,
            acao="excluir", antes={"escala_menor_id": menor, "escala_maior_id": maior},
        )
    db.commit()
