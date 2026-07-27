"""Ponte ORM <-> domínio.

O domínio (app/domain/) é puro: fala em dataclasses (`Militar`, `Escala`,
`Participacao`, `Impedimento`), não em linhas do banco. Este módulo traduz as
linhas ORM nessas estruturas e, sobretudo, DERIVA o que o domínio precisa mas
não guarda em coluna (regra: fonte única da verdade é `servico`):

  - `ultima_preta`/`ultima_vermelha` de cada participante (fila "mais folgado",
    regra 6.2) = max(dia) servido naquela cor NAQUELA escala, antes do dia-alvo;
  - `ultimo_termino_por_militar` (folga mínima entre concorrentes, regra 7.4) =
    max(termino_dt) do militar na escala e em suas concorrentes, antes do dia.

Premissa (regra 6/7): a escalação é cronológica — só o passado (`dia < alvo`)
alimenta a fila e a folga. Serviços do próprio dia-alvo não entram.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.domain.models import (
    Cor,
    Escala as EscalaDom,
    Impedimento as ImpedimentoDom,
    Militar as MilitarDom,
    Participacao as ParticipacaoDom,
)
from app.models.escala import EscalaConcorrente, Participacao, Posto
from app.models.escala import Escala as EscalaORM
from app.models.impedimento import Impedimento as ImpedimentoORM
from app.models.militar import Militar as MilitarORM
from app.models.referencia import PostoGraduacao
from app.models.servico import Servico


def militar_para_dominio(m: MilitarORM) -> MilitarDom:
    """Linha `militar` -> `domain.Militar`. Requer posto_graduacao, circulo e om.

    A ORDEM HIERÁRQUICA vai junto (`posto_ordem`/`posto_eh_praca`), não só a
    sigla: a tabela de postos é editável pelo gestor (Configurações), e sem
    isso o desempate da fila rodaria com a tabela da lei embutida no domínio —
    errado e silencioso numa OM que tenha reordenado ou acrescentado graduação.
    """
    pg = m.posto_graduacao
    return MilitarDom(
        id=m.id,
        nome_guerra=m.nome_guerra,
        nome_completo=m.nome_completo,
        posto=pg.sigla,
        om=m.om.sigla,
        posto_ordem=pg.ordem_hierarquica,
        posto_eh_praca=pg.circulo.eh_praca,
        data_promocao=m.data_promocao,
        data_praca=m.data_praca,
        ordem_manual=m.ordem_manual,
        numero_antiguidade=m.numero_antiguidade,
        data_nascimento=m.data_nascimento,
    )


def concorrentes_de(session: Session, escala_id: int) -> set[int]:
    """Ids das escalas concorrentes (relação simétrica, regra 7.4.1)."""
    linhas = session.execute(
        select(EscalaConcorrente.escala_menor_id, EscalaConcorrente.escala_maior_id)
        .where(
            (EscalaConcorrente.escala_menor_id == escala_id)
            | (EscalaConcorrente.escala_maior_id == escala_id)
        )
    ).all()
    return {maior if menor == escala_id else menor for menor, maior in linhas}


def escala_para_dominio(session: Session, e: EscalaORM) -> EscalaDom:
    """Linha `escala` -> `domain.Escala`, contando postos e concorrentes."""
    n_postos = session.scalar(
        select(func.count()).select_from(Posto).where(Posto.escala_id == e.id)
    ) or 0
    return EscalaDom(
        id=e.id,
        nome=e.nome,
        tem_preta=e.tem_preta,
        tem_vermelha=e.tem_vermelha,
        postos=n_postos,
        folga_minima_horas=e.folga_minima_horas,
        concorrentes=frozenset(concorrentes_de(session, e.id)),
        ativa=e.ativa,
        inicio_servico=e.inicio_servico,
        duracao_horas=e.duracao_horas,
    )


def participacoes_da_escala(
    session: Session, escala_id: int, ate_dia: date,
) -> list[ParticipacaoDom]:
    """Participantes da escala com a última data servida por cor (regra 6.2).

    `ate_dia` é o dia-alvo (exclusivo): a fila só olha o histórico anterior.
    """
    parts = session.execute(
        select(Participacao.militar_id, Participacao.ativo)
        .where(Participacao.escala_id == escala_id)
    ).all()
    ids = [mid for mid, _ in parts]
    if not ids:
        return []

    militares = {
        m.id: m
        for m in session.scalars(
            select(MilitarORM)
            .where(MilitarORM.id.in_(ids))
            # o círculo entra porque `posto_eh_praca` (regra 9.5) sai dele
            .options(
                joinedload(MilitarORM.posto_graduacao).joinedload(PostoGraduacao.circulo),
                joinedload(MilitarORM.om),
            )
        )
    }

    # última data servida por (militar, cor) NESTA escala, antes do dia-alvo
    ult = session.execute(
        select(Servico.militar_id, Servico.cor, func.max(Servico.dia))
        .where(Servico.escala_id == escala_id, Servico.dia < ate_dia)
        .group_by(Servico.militar_id, Servico.cor)
    ).all()
    idx: dict[tuple[int, Cor], date] = {(mid, cor): d for mid, cor, d in ult}

    return [
        ParticipacaoDom(
            militar=militar_para_dominio(militares[mid]),
            escala_id=escala_id,
            ultima_preta=idx.get((mid, Cor.PRETA)),
            ultima_vermelha=idx.get((mid, Cor.VERMELHA)),
            ativo=ativo,
        )
        for mid, ativo in parts
    ]


def ultimo_termino_por_militar(
    session: Session, escala: EscalaDom, militar_ids: list[int], antes_de_dia: date,
) -> dict[int, datetime]:
    """max(termino_dt) de cada militar na escala e suas concorrentes (regra 7.4).

    É o que faz as escalas conversarem: a folga mínima da escala de destino é
    medida contra o serviço mais recente do militar em QUALQUER cor e QUALQUER
    escala concorrente. Só o passado (`dia < antes_de_dia`) conta.
    """
    if not militar_ids:
        return {}
    relevantes = {escala.id, *escala.concorrentes}
    linhas = session.execute(
        select(Servico.militar_id, func.max(Servico.termino_dt))
        .where(
            Servico.escala_id.in_(relevantes),
            Servico.militar_id.in_(militar_ids),
            Servico.dia < antes_de_dia,
        )
        .group_by(Servico.militar_id)
    ).all()
    return {mid: termino for mid, termino in linhas}


def impedimentos_no_dia(
    session: Session, militar_ids: list[int], dia: date,
) -> list[ImpedimentoDom]:
    """Impedimentos que cobrem `dia` para os militares dados (regra 7.5)."""
    if not militar_ids:
        return []
    linhas = session.scalars(
        select(ImpedimentoORM).where(
            ImpedimentoORM.militar_id.in_(militar_ids),
            ImpedimentoORM.inicio <= dia,
            ImpedimentoORM.fim >= dia,
        )
    ).all()
    return [
        ImpedimentoDom(
            militar_id=r.militar_id, inicio=r.inicio, fim=r.fim, obs=r.observacao or "",
        )
        for r in linhas
    ]
