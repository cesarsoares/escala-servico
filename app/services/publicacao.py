"""Monta o documento da escala para publicação (regra 12).

Saída única, consumida hoje pela página de impressão (HTML + CSS `@media print`,
que o navegador salva em PDF) e, quando o WeasyPrint entrar no container, pelo
mesmo caminho — o documento é o mesmo, muda só o renderizador.

Mostra QUEM SERVE no dia: o escalado e, havendo permuta, quem o cobre. A folga
continua sendo do escalado (regra 9) — a permuta é registro puro, não muda a
fila; por isso o documento exibe os dois nomes em vez de trocar um pelo outro.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.domain.calendario import classificar_dia
from app.models.escala import Escala, Posto
from app.models.militar import Militar
from app.models.servico import Permuta, Servico
from app.services import calendario_service

MESES = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
DIAS_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


@dataclass
class LinhaPosto:
    """Uma vaga do dia: quem está escalado (e quem cobre, se houve permuta)."""
    rotulo: str
    militar: str            # 'Cap SILVA (Cmdo CMS)'
    substituto: str | None  # preenchido só quando há permuta


@dataclass
class LinhaDia:
    dia: date
    semana: str
    cor: str
    feriado: bool
    postos: list[LinhaPosto]


def _nome(m: Militar) -> str:
    return f"{m.posto_graduacao.sigla} {m.nome_guerra} ({m.om.sigla})"


def documento(db: Session, escala: Escala, ano: int, mes: int) -> list[LinhaDia]:
    """Dias escalados do mês, em ordem. Dia sem serviço não entra (uma escala só
    vermelha imprimiria três semanas de linhas vazias)."""
    primeiro = date(ano, mes, 1)
    ultimo = date(ano, mes, calendar.monthrange(ano, mes)[1])

    servicos = db.scalars(
        select(Servico)
        .where(Servico.escala_id == escala.id, Servico.dia >= primeiro, Servico.dia <= ultimo)
        .options(
            joinedload(Servico.militar).joinedload(Militar.posto_graduacao),
            joinedload(Servico.militar).joinedload(Militar.om),
        )
        .order_by(Servico.dia, Servico.posto_id)
    ).all()

    rotulos = {
        p.id: (p.rotulo or (f"{p.ordem}º posto" if len(escala.postos) > 1 else "Serviço"))
        for p in db.scalars(select(Posto).where(Posto.escala_id == escala.id))
    }

    # Substitutos: Permuta não tem relação para o militar, então carrego à parte.
    ids = [s.id for s in servicos]
    permutas = {
        p.servico_id: p.militar_substituto_id
        for p in (db.scalars(select(Permuta).where(Permuta.servico_id.in_(ids))) if ids else [])
    }
    substitutos = {
        m.id: m for m in (db.scalars(
            select(Militar)
            .options(joinedload(Militar.posto_graduacao), joinedload(Militar.om))
            .where(Militar.id.in_(set(permutas.values())))
        ) if permutas else [])
    }

    feriados = calendario_service.feriados(db, primeiro, ultimo)
    ov_verm = calendario_service.overrides_vermelha(db, primeiro, ultimo)
    ov_preta = calendario_service.overrides_preta(db, primeiro, ultimo)

    dias: dict[date, LinhaDia] = {}
    for s in servicos:
        linha = dias.get(s.dia)
        if linha is None:
            linha = dias[s.dia] = LinhaDia(
                dia=s.dia,
                semana=DIAS_SEMANA[s.dia.weekday()],
                cor=classificar_dia(s.dia, feriados, ov_verm, ov_preta).value,
                feriado=s.dia in feriados,
                postos=[],
            )
        sub = substitutos.get(permutas.get(s.id))
        linha.postos.append(LinhaPosto(
            rotulo=rotulos.get(s.posto_id, "Serviço"),
            militar=_nome(s.militar),
            substituto=_nome(sub) if sub else None,
        ))
    return [dias[d] for d in sorted(dias)]
