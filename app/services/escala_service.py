"""Operações de escala que envolvem regra/normalização (não só CRUD cru).

Por ora: a concorrência entre escalas (regra 7.4.1). A relação é simétrica e o
modelo guarda um par ordenado (escala_menor_id < escala_maior_id) para não
duplicar a simetria — este serviço normaliza a ordem e é idempotente.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.escala import Escala as EscalaORM, EscalaConcorrente
from app.services import mapeamento


def declarar_concorrencia(session: Session, escala_a_id: int, escala_b_id: int) -> EscalaConcorrente:
    """Declara duas escalas como concorrentes (regra 7.4.1). Ordem indiferente.

    Normaliza para menor<maior (respeita a CheckConstraint ck_par_ordenado) e não
    duplica se o par já existir. Levanta ValueError se forem iguais ou se alguma
    escala não existir.
    """
    if escala_a_id == escala_b_id:
        raise ValueError("uma escala não concorre consigo mesma")

    menor, maior = sorted((escala_a_id, escala_b_id))
    for eid in (menor, maior):
        if session.get(EscalaORM, eid) is None:
            raise ValueError(f"escala {eid} não encontrada")

    existente = session.get(EscalaConcorrente, (menor, maior))
    if existente is not None:
        return existente

    par = EscalaConcorrente(escala_menor_id=menor, escala_maior_id=maior)
    session.add(par)
    session.flush()
    return par


def remover_concorrencia(session: Session, escala_a_id: int, escala_b_id: int) -> bool:
    """Desfaz a concorrência entre duas escalas. Idempotente (False se não havia)."""
    menor, maior = sorted((escala_a_id, escala_b_id))
    par = session.get(EscalaConcorrente, (menor, maior))
    if par is None:
        return False
    session.delete(par)
    session.flush()
    return True


def concorrentes_de(session: Session, escala_id: int) -> list[int]:
    """Ids das escalas concorrentes de `escala_id` (relação simétrica), ordenados.

    Fonte única da query: `mapeamento.concorrentes_de` (usado pelo motor). Aqui só
    ordenamos para uma saída estável de API.
    """
    return sorted(mapeamento.concorrentes_de(session, escala_id))
