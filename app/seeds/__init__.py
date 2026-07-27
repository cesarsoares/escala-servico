"""Seed dos dados fixos (regras 5.2, 7.5, 9.x; Lei 6.880/80 art. 16).

Popula as tabelas de referência de forma IDEMPOTENTE (rodar de novo não duplica):
  - circulo_hierarquico e posto_graduacao (fixos por lei);
  - tipo_impedimento (sugestões; o gestor pode criar outros);
  - feriado nacional (para um intervalo de anos).

Uso: python -m app.seeds            # anos: atual e os 2 seguintes
     python -m app.seeds 2025 2030  # intervalo explícito
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CirculoHierarquico,
    Feriado,
    PostoGraduacao,
    TipoImpedimento,
)
from app.seeds.dados import CIRCULOS, POSTOS_GRADUACAO, TIPOS_IMPEDIMENTO
from app.seeds.feriados import feriados_nacionais


def seed_circulos(db: Session) -> int:
    existentes = {c.nome for c in db.scalars(select(CirculoHierarquico)).all()}
    novos = 0
    for nome, ordem, eh_praca in CIRCULOS:
        if nome not in existentes:
            db.add(CirculoHierarquico(nome=nome, ordem=ordem, eh_praca=eh_praca))
            novos += 1
    db.flush()
    return novos


def seed_postos_graduacao(db: Session) -> int:
    circulos = {c.nome: c.id for c in db.scalars(select(CirculoHierarquico)).all()}
    existentes = {p.sigla for p in db.scalars(select(PostoGraduacao)).all()}
    novos = 0
    for sigla, nome, ordem, circulo_nome in POSTOS_GRADUACAO:
        if sigla not in existentes:
            db.add(PostoGraduacao(
                sigla=sigla, nome=nome, ordem_hierarquica=ordem,
                circulo_id=circulos[circulo_nome],
            ))
            novos += 1
    db.flush()
    return novos


def seed_tipos_impedimento(db: Session) -> int:
    existentes = {t.nome for t in db.scalars(select(TipoImpedimento)).all()}
    novos = 0
    for nome in TIPOS_IMPEDIMENTO:
        if nome not in existentes:
            db.add(TipoImpedimento(nome=nome))
            novos += 1
    db.flush()
    return novos


def seed_feriados(db: Session, anos: range) -> int:
    existentes = {f.data for f in db.scalars(select(Feriado)).all()}
    novos = 0
    for ano in anos:
        for data, nome in feriados_nacionais(ano).items():
            if data not in existentes:
                db.add(Feriado(data=data, nome=nome, nacional=True))
                existentes.add(data)
                novos += 1
    db.flush()
    return novos


def run(db: Session, anos: range) -> dict[str, int]:
    """Roda todos os seeds (idempotente) e faz commit. Retorna a contagem de novos."""
    resultado = {
        "circulos": seed_circulos(db),
        "postos_graduacao": seed_postos_graduacao(db),
        "tipos_impedimento": seed_tipos_impedimento(db),
        "feriados": seed_feriados(db, anos),
    }
    db.commit()
    return resultado


def _anos_padrao() -> range:
    atual = date.today().year
    return range(atual, atual + 3)
