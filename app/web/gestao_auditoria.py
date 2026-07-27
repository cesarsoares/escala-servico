"""Tela do histórico de alterações (HTML, protegida — regra 11).

A regra 11 exige histórico de TODAS as alterações manuais. O dado já era gravado
por `services/auditoria` em toda mutação e havia a API `/api/auditoria`; faltava
onde o gestor lesse — o painel só mostra as 8 últimas.

O que esta tela acrescenta à API: mostra **o que mudou** (campo: antes → depois)
em vez de dois JSON para comparar a olho, e traduz o carimbo de tempo do banco
(UTC) para o fuso de quem lê.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.gestao import Auditoria, Usuario
from app.services import auditoria as auditoria_service
from app.web import templates
from app.web.gestao import gestor_web

router = APIRouter(prefix="/gestao", tags=["web-gestão"])

POR_PAGINA = 50


def _janela_utc(dia: date, fim_do_dia: bool) -> datetime:
    """Data escolhida na tela (fuso do servidor) -> instante em UTC naive.

    A coluna é UTC sem tzinfo; filtrar com a data crua traria o dia errado nas
    primeiras/últimas horas. 'até' inclui o dia inteiro.
    """
    momento = datetime.combine(dia, time.max if fim_do_dia else time.min)
    return momento.astimezone(timezone.utc).replace(tzinfo=None)


def _data_do_form(texto: str | None) -> date | None:
    try:
        return date.fromisoformat((texto or "").strip())
    except ValueError:
        return None


@router.get("/auditoria", response_class=HTMLResponse)
def auditoria(
    request: Request,
    entidade: str = Query(""),
    acao: str = Query(""),
    usuario_id: str = Query(""),
    desde: str = Query(""),
    ate: str = Query(""),
    pagina: int = Query(1, ge=1),
    db: Session = Depends(get_db),
    gestor: Usuario = Depends(gestor_web),
):
    """Histórico filtrável, do mais recente para o mais antigo (regra 11)."""
    stmt = select(Auditoria).order_by(Auditoria.criado_em.desc(), Auditoria.id.desc())
    if entidade:
        stmt = stmt.where(Auditoria.entidade == entidade)
    if acao:
        stmt = stmt.where(Auditoria.acao == acao)
    if usuario_id.strip().isdigit():
        stmt = stmt.where(Auditoria.usuario_id == int(usuario_id))
    d_desde, d_ate = _data_do_form(desde), _data_do_form(ate)
    if d_desde:
        stmt = stmt.where(Auditoria.criado_em >= _janela_utc(d_desde, fim_do_dia=False))
    if d_ate:
        stmt = stmt.where(Auditoria.criado_em <= _janela_utc(d_ate, fim_do_dia=True))

    # Pede um a mais que a página para saber se existe 'próxima' sem um COUNT
    # à parte (a tabela cresce sem teto e o count varreria tudo).
    offset = (pagina - 1) * POR_PAGINA
    achados = list(db.scalars(stmt.limit(POR_PAGINA + 1).offset(offset)))
    tem_proxima = len(achados) > POR_PAGINA
    achados = achados[:POR_PAGINA]

    # Resumo do histórico INTEIRO (não da página): serve para o gestor saber
    # onde procurar antes de filtrar.
    def resumo(coluna):
        linhas = db.execute(
            select(coluna, func.count()).group_by(coluna).order_by(func.count().desc())
        ).all()
        maior = max((n for _, n in linhas), default=0)
        return [{"rotulo": v, "n": n, "pct": 100 if not maior else round(n * 100 / maior)}
                for v, n in linhas]

    usuarios = {u.id: u for u in db.scalars(select(Usuario).order_by(Usuario.nome))}
    linhas = [
        {
            "registro": reg,
            "quem": usuarios[reg.usuario_id].nome if reg.usuario_id in usuarios else None,
            "mudancas": auditoria_service.diferencas(reg.dados_antes, reg.dados_depois),
        }
        for reg in achados
    ]
    return templates.TemplateResponse(request, "gestao/auditoria.html", {
        "gestor": gestor, "linhas": linhas, "pagina": pagina, "tem_proxima": tem_proxima,
        "usuarios": list(usuarios.values()),
        # Só as entidades/ações que existem no histórico — filtro que não devolve
        # nada é ruído.
        "entidades": list(db.scalars(
            select(Auditoria.entidade).distinct().order_by(Auditoria.entidade))),
        "acoes": list(db.scalars(
            select(Auditoria.acao).distinct().order_by(Auditoria.acao))),
        "por_acao": resumo(Auditoria.acao),
        "por_entidade": resumo(Auditoria.entidade),
        "f": {"entidade": entidade, "acao": acao, "usuario_id": usuario_id,
              "desde": desde, "ate": ate},
        "querystring": _querystring(entidade, acao, usuario_id, desde, ate),
    })


def _querystring(entidade, acao, usuario_id, desde, ate) -> str:
    """Filtros em vigor, para os links de página não os perderem."""
    partes = [f"{nome}={valor}" for nome, valor in (
        ("entidade", entidade), ("acao", acao), ("usuario_id", usuario_id),
        ("desde", desde), ("ate", ate)) if valor]
    return ("&" + "&".join(partes)) if partes else ""
