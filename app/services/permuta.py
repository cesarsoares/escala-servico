"""Permutas / trocas (seção 9/10 das regras).

Registro PURO: a permuta apenas anota que outro militar cobriu um serviço. NÃO
há retribuição automática nem recálculo de folga — a folga segue colada em
`servico.militar_id` (o ESCALADO), nunca no substituto (regra 9).

A única regra de negócio ao registrar é de GUARDA: nega-se a permuta se ela
ferir a folga mínima de quem vai cobrir (regra 10.5), medida com os mesmos
critérios do motor — janela da escala de destino, folga da escala de destino,
serviço mais recente do substituto em qualquer cor e escala concorrente
(regra 7.4). Um substituto impedido no dia também não pode cobrir (regra 7.5).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.folga import respeita_folga_minima
from app.models.escala import Escala as EscalaORM
from app.models.militar import Militar as MilitarORM
from app.models.servico import Permuta, Servico
from app.services import mapeamento


class PermutaNegada(Exception):
    """Permuta recusada por regra de negócio (regra 10.5). Carrega o motivo."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


def registrar_permuta(
    session: Session,
    servico_id: int,
    militar_substituto_id: int,
    autorizado_por: int | None = None,
    observacao: str | None = None,
) -> Permuta:
    """Registra que `militar_substituto_id` cobre o serviço `servico_id`.

    Levanta ValueError se serviço/militar não existem; PermutaNegada se a troca
    fere uma regra (substituto = escalado, serviço já permutado, folga mínima ou
    impedimento do substituto). A folga NÃO é recalculada (regra 9).
    """
    servico = session.get(Servico, servico_id)
    if servico is None:
        raise ValueError(f"serviço {servico_id} não encontrado")

    substituto = session.get(MilitarORM, militar_substituto_id)
    if substituto is None:
        raise ValueError(f"militar {militar_substituto_id} não encontrado")

    if servico.militar_id == militar_substituto_id:
        raise PermutaNegada("o substituto é o próprio militar escalado")

    # consulta direta (não a relação servico.permuta, que pode estar em cache)
    ja_existe = session.scalar(select(Permuta).where(Permuta.servico_id == servico_id))
    if ja_existe is not None:
        raise PermutaNegada("este serviço já possui permuta registrada")

    _garantir_disponibilidade(session, servico, militar_substituto_id)

    permuta = Permuta(
        servico_id=servico_id,
        militar_substituto_id=militar_substituto_id,
        autorizado_por=autorizado_por,
        observacao=observacao,
    )
    session.add(permuta)
    session.flush()
    return permuta


def cancelar_permuta(session: Session, servico_id: int) -> bool:
    """Remove a permuta de um serviço (o escalado volta a figurar). Idempotente."""
    permuta = session.scalar(select(Permuta).where(Permuta.servico_id == servico_id))
    if permuta is None:
        return False
    session.delete(permuta)
    session.flush()
    return True


def _garantir_disponibilidade(session: Session, servico: Servico, substituto_id: int) -> None:
    """Aplica as regras de guarda ao substituto (regras 7.4/7.5, 10.5).

    Usa a MESMA aritmética do motor: janela e folga são as da escala do serviço;
    o `ultimo_termino` do substituto varre a escala e suas concorrentes, só o
    passado (`dia < servico.dia`). Nota: uma escalação do substituto no MESMO dia
    em escala concorrente não é capturada (o motor também usa `dia <`); é caso de
    borda fora da regra escrita.
    """
    e_orm = session.get(EscalaORM, servico.escala_id)
    escala = mapeamento.escala_para_dominio(session, e_orm)

    impedimentos = mapeamento.impedimentos_no_dia(session, [substituto_id], servico.dia)
    if impedimentos:
        raise PermutaNegada("o substituto está impedido no dia do serviço")

    # duplo-agendamento no MESMO dia: a folga (dia<servico.dia) não pega isto.
    # Se o substituto já serve nesse dia na escala ou numa concorrente, cobriria
    # dois serviços de 24h simultâneos (regra 7.4).
    relevantes = {escala.id, *escala.concorrentes}
    ja_serve_hoje = session.scalar(
        select(Servico.id).where(
            Servico.militar_id == substituto_id,
            Servico.dia == servico.dia,
            Servico.escala_id.in_(relevantes),
            Servico.id != servico.id,
        )
    )
    if ja_serve_hoje is not None:
        raise PermutaNegada("o substituto já está de serviço nesse dia")

    ultimo = mapeamento.ultimo_termino_por_militar(
        session, escala, [substituto_id], antes_de_dia=servico.dia,
    ).get(substituto_id)

    if not respeita_folga_minima(ultimo, servico.inicio_dt, escala.folga_minima_horas):
        raise PermutaNegada(
            "a permuta feriria a folga mínima do substituto (regra 10.5)"
        )
