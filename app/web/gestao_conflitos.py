"""Tela de conflito entre serviço gravado e impedimento (regra 7.5).

A demanda que a originou (Brigada, 30/07): "mesmo colocado impedimento o militar
ainda é escalado no período". O motor não escala impedido — o que acontece é a
dispensa chegar DEPOIS do mês fechado, e o que está gravado não se desfaz
sozinho. A única saída era 'regravar' o período inteiro, que refaz a escala toda
e leva as permutas junto; numa escala já publicada isso não serve.

Aqui a resolução é **dia a dia**: o sistema propõe o próximo da fila, o gestor
confirma, e nada mais no mês se mexe. A lógica e as guardas moram em
`services/conflitos.py` — esta camada só pergunta e grava.

Router separado (mesmo prefixo /gestao) pelo mesmo motivo dos outros: tamanho.
Não ganhou aba na barra de navegação, que já estourou duas vezes — chega-se pelo
painel, pela tela de impedimentos e pelo aviso de quem acaba de lançar um.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.escala import Participacao
from app.models.gestao import Usuario
from app.models.militar import Militar
from app.models.servico import Servico
from app.services import auditoria, conflitos as servico_conflitos
from app.services.publicacao import DIAS_SEMANA
from app.web import templates
from app.web.gestao import agrupar_por_posto, gestor_web

router = APIRouter(prefix="/gestao", tags=["web-gestão"])


def _data(texto: str | None) -> date | None:
    """Data da querystring; ilegível é IGNORADA, nunca 500 (como no histórico)."""
    try:
        return date.fromisoformat(texto) if texto else None
    except ValueError:
        return None


FILTROS = ("militar_id", "escala_id", "de", "ate")


def _inteiro(texto: str | None) -> int | None:
    return int(texto) if texto and texto.lstrip("-").isdigit() else None


def _tela(request: Request, db: Session, gestor: Usuario, filtros: dict,
          erro: str | None = None, status: int = 200):
    """Monta a lista de conflitos com os filtros dados.

    Usada pelo GET e pelo POST que falha: a recusa é renderizada DIRETO, não
    devolvida na URL. Motivo de recusa é texto livre vindo do serviço — passá-lo
    em `?erro=` deixaria qualquer link escrever o que quisesse na tela de gestão
    (a mesma razão pela qual as confirmações são chaves em AVISOS, e chave
    desconhecida não exibe nada).
    """
    achados = servico_conflitos.conflitos(
        db,
        militar_id=filtros.get("militar_id"), escala_id=filtros.get("escala_id"),
        de=_data(filtros.get("de")), ate=_data(filtros.get("ate")),
    )
    foco = db.scalar(
        select(Militar).options(joinedload(Militar.posto_graduacao))
        .where(Militar.id == filtros["militar_id"])
    ) if filtros.get("militar_id") is not None else None
    return templates.TemplateResponse(request, "gestao/conflitos.html", {
        "gestor": gestor, "conflitos": achados, "foco": foco, "erro": erro,
        "dias_semana": DIAS_SEMANA, "hoje": date.today(), "filtros": filtros,
    }, status_code=status)


def _filtros_da_url(request: Request) -> dict:
    """Os filtros vêm na querystring TAMBÉM no POST (a action os carrega).

    É o que permite voltar para a mesma lista filtrada: sem isso, resolver um
    conflito a partir da ficha de um militar jogaria o gestor na lista geral, e
    ele teria de reencontrar os outros dias do mesmo impedimento.
    """
    q = request.query_params
    return {
        "militar_id": _inteiro(q.get("militar_id")),
        "escala_id": _inteiro(q.get("escala_id")),
        "de": q.get("de") or "",
        "ate": q.get("ate") or "",
    }


@router.get("/conflitos", response_class=HTMLResponse)
def lista_conflitos(
    request: Request,
    militar_id: int | None = None,
    escala_id: int | None = None,
    de: str | None = None,
    ate: str | None = None,
    db: Session = Depends(get_db),
    gestor: Usuario = Depends(gestor_web),
):
    """Serviços gravados cujo escalado está impedido no dia.

    Sem filtro mostra tudo — inclusive o passado, de propósito: um serviço que
    ficou no nome de quem estava de férias é registro errado mesmo depois de
    vencido, e o documento publicado saiu com ele.
    """
    return _tela(request, db, gestor, {
        "militar_id": militar_id, "escala_id": escala_id,
        "de": de or "", "ate": ate or "",
    })


def _volta(request: Request, ok: str) -> str:
    """URL da mesma lista filtrada, com a confirmação (chave de AVISOS)."""
    from urllib.parse import urlencode
    query = [(k, v) for k, v in request.query_params.multi_items() if k in FILTROS]
    query.append(("ok", ok))
    return "/gestao/conflitos?" + urlencode(query)


@router.post("/conflitos/servico/{servico_id}/substituir")
def substituir(
    request: Request,
    servico_id: int,
    militar_id: int = Form(...),
    db: Session = Depends(get_db),
    gestor: Usuario = Depends(gestor_web),
):
    """Troca o escalado deste serviço pelo militar indicado (regra 6.1/7.4)."""
    filtros = _filtros_da_url(request)
    servico = db.get(Servico, servico_id)
    if servico is None:
        return _tela(request, db, gestor, filtros, "Serviço não encontrado.", 404)

    antes = servico_conflitos.retrato(servico)
    try:
        servico_conflitos.substituir(db, servico_id, militar_id)
    except (servico_conflitos.SubstituicaoNegada, ValueError) as e:
        db.rollback()
        motivo = getattr(e, "motivo", None) or "Militar não encontrado."
        return _tela(request, db, gestor, filtros, motivo, 400)

    auditoria.registrar(
        db, usuario_id=gestor.id, entidade="servico", entidade_id=servico_id,
        acao="substituir", antes=antes, depois=servico_conflitos.retrato(servico),
    )
    db.commit()
    return RedirectResponse(_volta(request, ok="conflito-resolvido"), status_code=303)


@router.post("/conflitos/servico/{servico_id}/descobrir")
def descobrir(
    request: Request,
    servico_id: int,
    db: Session = Depends(get_db),
    gestor: Usuario = Depends(gestor_web),
):
    """Apaga o serviço, deixando a vaga vazia (regra 7.8) — quando não há quem entre."""
    servico = db.get(Servico, servico_id)
    if servico is None:
        return _tela(request, db, gestor, _filtros_da_url(request),
                     "Serviço não encontrado.", 404)

    antes = servico_conflitos.retrato(servico)
    servico_conflitos.descobrir(db, servico_id)
    auditoria.registrar(
        db, usuario_id=gestor.id, entidade="servico", entidade_id=servico_id,
        acao="descobrir", antes=antes,
    )
    db.commit()
    return RedirectResponse(_volta(request, ok="vaga-descoberta"), status_code=303)


@router.get("/conflitos/servico/{servico_id}", response_class=HTMLResponse)
def escolher_substituto(
    request: Request,
    servico_id: int,
    db: Session = Depends(get_db),
    gestor: Usuario = Depends(gestor_web),
):
    """Tela de propósito para escolher OUTRO militar que não o proposto.

    Separada da lista pelo mesmo motivo das permutas: um `<select>` do efetivo em
    cada linha renderiza milhares de `<option>` numa tela que costuma ter dez ou
    quinze conflitos.
    """
    servico = db.scalar(
        select(Servico).options(
            joinedload(Servico.militar).joinedload(Militar.posto_graduacao))
        .where(Servico.id == servico_id)
    )
    if servico is None:
        return RedirectResponse("/gestao/conflitos", status_code=303)

    achado = next(
        (c for c in servico_conflitos.conflitos(db, escala_id=servico.escala_id,
                                                de=servico.dia, ate=servico.dia)
         if c.servico_id == servico_id),
        None,
    )
    # Candidatos = participantes ativos da escala, menos o escalado. As guardas
    # (impedimento, folga, dobra) rodam na gravação: aqui a lista é ampla de
    # propósito — o gestor precisa ver quem existe para entender a recusa.
    candidatos = db.scalars(
        select(Militar).options(joinedload(Militar.posto_graduacao))
        .join(Participacao, Participacao.militar_id == Militar.id)
        .where(Participacao.escala_id == servico.escala_id,
               Participacao.ativo.is_(True),
               Militar.id != servico.militar_id)
    ).all()
    return templates.TemplateResponse(request, "gestao/conflito_form.html", {
        "gestor": gestor, "servico": servico, "conflito": achado,
        "candidatos": agrupar_por_posto(candidatos),
        "dias_semana": DIAS_SEMANA,
    })
