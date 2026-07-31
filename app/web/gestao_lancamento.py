"""Tela de lançamento manual de serviço (demanda do Brigada, 30/07).

"Se tiver que manusear a escala sem passar pelo sistema, em caso de emergência,
poderá ser feito um registro no sistema." É a porta para o serviço que aconteceu
fora do sistema, e para corrigir o que está gravado errado — inclusive a data,
que é o que ele pediu textualmente.

A regra e as guardas moram em `services/lancamento.py`; aqui só se pergunta,
mostra e grava. Conferir → confirmar em UMA rota, pelo campo `confirmado`: se há
avisos e ninguém confirmou, a tela volta com eles e um botão de "gravar assim
mesmo". Erro que impede não tem botão nenhum.

Router separado (mesmo prefixo /gestao). Sem aba nova na barra de navegação, que
já estourou duas vezes: chega-se pela tela Escalar, pela tela da escala e pelo
manual.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.escala import Escala, Participacao, Posto
from app.models.gestao import Usuario
from app.models.militar import Militar
from app.models.servico import Permuta, Servico
from app.services import auditoria, lancamento
from app.services.publicacao import DIAS_SEMANA, MESES
from app.web import ANO_MAX, ANO_MIN, templates
from app.web.gestao import agrupar_por_posto, gestor_web

router = APIRouter(prefix="/gestao", tags=["web-gestão"])


def _candidatos(db: Session, escala: Escala | None, incluir_id: int | None) -> list[Militar]:
    """Participantes ATIVOS da escala, para o `<select>` de militar.

    Era o efetivo inteiro. O Brigada reclamou com razão (30/07): abrir o Museu,
    que tem 11 participantes, e receber 285 nomes torna o campo inutilizável — e
    quem deve concorrer se inclui na própria escala (tela de participantes), que
    é onde ele já administra isso.

    `incluir_id` é o escalado da linha que está sendo CORRIGIDA: ele entra mesmo
    que hoje não seja mais participante — ou esteja desativado — porque abrir
    "corrigir" para acertar só a data não pode trocar a pessoa por engano. Fora
    esse caso, os avisos de `lancamento.analisar` (não é participante, isento,
    cor em que não concorre) seguem valendo para a API e para o CSV.
    """
    if escala is None:
        return []
    participa = and_(Participacao.militar_id == Militar.id,
                     Participacao.escala_id == escala.id,
                     Participacao.ativo.is_(True))
    return list(db.scalars(
        select(Militar)
        .outerjoin(Participacao, participa)
        .options(joinedload(Militar.posto_graduacao))
        .where(or_(and_(Militar.ativo.is_(True), Participacao.id.isnot(None)),
                   Militar.id == incluir_id))
        .order_by(Militar.nome_guerra)
    ).unique())


def _contexto(db: Session, escala_id: int | None, ano: int, mes: int,
              incluir_militar_id: int | None = None) -> dict:
    """Lista do mês da escala escolhida + o que os formulários precisam."""
    escalas = db.scalars(select(Escala).order_by(Escala.ativa.desc(), Escala.nome)).all()
    escala = next((e for e in escalas if e.id == escala_id), None)
    if escala is None:
        escala = next((e for e in escalas if e.ativa), escalas[0] if escalas else None)

    servicos, postos, permutados = [], [], set()
    if escala is not None:
        primeiro = date(ano, mes, 1)
        ultimo = date(ano + (mes == 12), (mes % 12) + 1, 1)
        servicos = db.scalars(
            select(Servico)
            .options(joinedload(Servico.militar).joinedload(Militar.posto_graduacao))
            .where(Servico.escala_id == escala.id,
                   Servico.dia >= primeiro, Servico.dia < ultimo)
            .order_by(Servico.dia, Servico.posto_id)
        ).all()
        postos = db.scalars(
            select(Posto).where(Posto.escala_id == escala.id).order_by(Posto.ordem)).all()
        if servicos:
            permutados = {
                sid for (sid,) in db.execute(
                    select(Permuta.servico_id)
                    .where(Permuta.servico_id.in_([s.id for s in servicos]))).all()
            }

    return {
        "escalas": escalas, "escala": escala, "servicos": servicos, "postos": postos,
        # rótulo do posto na tabela: o id cru não diz nada a quem confere o mês
        "rotulo_posto": {p.id: (p.rotulo or f"Posto {p.ordem}") for p in postos},
        "permutados": permutados,
        "militares": agrupar_por_posto(_candidatos(db, escala, incluir_militar_id)),
        # MESES tem "" na posição 0 (é indexado pelo número do mês): com
        # `mes - 1` o título saía um mês atrasado, e em janeiro saía VAZIO.
        "ano": ano, "mes": mes, "mes_nome": MESES[mes], "dias_semana": DIAS_SEMANA,
        "hoje": date.today(),
    }


def _tela(request, db, gestor, escala_id, ano, mes, **extra):
    status = extra.pop("status", 200)
    editando = extra.get("editando")
    ctx = _contexto(db, escala_id, ano, mes,
                    incluir_militar_id=editando.militar_id if editando else None)
    ctx.update({"gestor": gestor, "analise": None, "form": {}, "erro": None,
                "editando": None})
    ctx.update(extra)
    return templates.TemplateResponse(request, "gestao/lancamento.html", ctx,
                                      status_code=status)


@router.get("/servicos", response_class=HTMLResponse)
def tela_servicos(
    request: Request,
    escala_id: int | None = None,
    ano: int = 0,
    mes: int = 0,
    editar: int | None = None,
    db: Session = Depends(get_db),
    gestor: Usuario = Depends(gestor_web),
):
    """Serviços do mês, com o formulário de lançamento. `?editar=` abre uma linha.

    Mês/ano fora da faixa caem no corrente em vez de 422: esta tela se navega por
    setas, e um link de mês colado errado não deve virar erro de sistema. A faixa
    de ano existe porque `date(ano, mes, 1)` estoura em 0 (o mesmo motivo de
    ANO_MIN/ANO_MAX na consulta aberta).
    """
    hoje = date.today()
    ano = ano if ANO_MIN <= ano <= ANO_MAX else hoje.year
    mes = mes if 1 <= mes <= 12 else hoje.month

    editando = db.get(Servico, editar) if editar else None
    form = {}
    if editando is not None:
        escala_id = editando.escala_id
        ano, mes = editando.dia.year, editando.dia.month
        form = {"dia": editando.dia.isoformat(), "militar_id": editando.militar_id,
                "posto_id": editando.posto_id}
    return _tela(request, db, gestor, escala_id, ano, mes,
                 editando=editando, form=form)


@router.post("/servicos", response_class=HTMLResponse)
def lancar(
    request: Request,
    escala_id: int = Form(...),
    posto_id: int = Form(...),
    dia: str = Form(...),
    militar_id: int = Form(...),
    ano: int = Form(...),
    mes: int = Form(...),
    confirmado: str | None = Form(None),
    db: Session = Depends(get_db),
    gestor: Usuario = Depends(gestor_web),
):
    """Etapa 1 (conferir) e 2 (confirmar) na mesma rota, pelo campo `confirmado`."""
    form = {"dia": dia, "militar_id": militar_id, "posto_id": posto_id}
    try:
        d = date.fromisoformat(dia)
    except ValueError:
        return _tela(request, db, gestor, escala_id, ano, mes,
                     erro="Data inválida.", form=form, status=400)

    analise = lancamento.analisar(db, escala_id, posto_id, d, militar_id)
    # Erro impede sempre; aviso só na primeira passada — depois de confirmado, é
    # decisão tomada (fato consumado, como na importação de CSV).
    if not analise.pode_gravar or (analise.avisos and not confirmado):
        return _tela(request, db, gestor, escala_id, ano, mes,
                     analise=analise, form=form,
                     status=400 if not analise.pode_gravar else 200)

    servico = lancamento.lancar(db, escala_id, posto_id, d, militar_id)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="servico",
                        entidade_id=servico.id, acao="lancar",
                        depois=lancamento.retrato(servico))
    db.commit()
    return RedirectResponse(
        f"/gestao/servicos?escala_id={escala_id}&ano={d.year}&mes={d.month}"
        "&ok=servico-lancado", status_code=303)


@router.post("/servicos/{servico_id}/alterar", response_class=HTMLResponse)
def alterar(
    request: Request,
    servico_id: int,
    dia: str = Form(...),
    militar_id: int = Form(...),
    posto_id: int = Form(...),
    ano: int = Form(...),
    mes: int = Form(...),
    confirmado: str | None = Form(None),
    db: Session = Depends(get_db),
    gestor: Usuario = Depends(gestor_web),
):
    """Corrige data, escalado e/ou posto de um serviço já gravado."""
    servico = db.get(Servico, servico_id)
    if servico is None:
        return RedirectResponse("/gestao/servicos", status_code=303)

    form = {"dia": dia, "militar_id": militar_id, "posto_id": posto_id}
    try:
        d = date.fromisoformat(dia)
    except ValueError:
        return _tela(request, db, gestor, servico.escala_id, ano, mes,
                     erro="Data inválida.", editando=servico, form=form, status=400)

    analise = lancamento.analisar(db, servico.escala_id, posto_id, d, militar_id,
                                  ignorar_servico_id=servico_id)
    if not analise.pode_gravar or (analise.avisos and not confirmado):
        return _tela(request, db, gestor, servico.escala_id, ano, mes,
                     analise=analise, editando=servico, form=form,
                     status=400 if not analise.pode_gravar else 200)

    antes = lancamento.retrato(servico)
    try:
        lancamento.alterar(db, servico_id, d, militar_id, posto_id)
    except lancamento.LancamentoNegado as e:
        db.rollback()
        return _tela(request, db, gestor, servico.escala_id, ano, mes,
                     erro="; ".join(e.motivos), editando=db.get(Servico, servico_id),
                     form=form, status=400)

    auditoria.registrar(db, usuario_id=gestor.id, entidade="servico",
                        entidade_id=servico_id, acao="alterar", antes=antes,
                        depois=lancamento.retrato(servico))
    db.commit()
    return RedirectResponse(
        f"/gestao/servicos?escala_id={servico.escala_id}&ano={d.year}&mes={d.month}"
        "&ok=servico-alterado", status_code=303)


@router.post("/servicos/{servico_id}/remover")
def remover(
    servico_id: int,
    ano: int = Form(...),
    mes: int = Form(...),
    db: Session = Depends(get_db),
    gestor: Usuario = Depends(gestor_web),
):
    servico = db.get(Servico, servico_id)
    if servico is None:
        return RedirectResponse("/gestao/servicos", status_code=303)
    escala_id = servico.escala_id
    antes = lancamento.retrato(servico)
    lancamento.remover(db, servico_id)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="servico",
                        entidade_id=servico_id, acao="excluir", antes=antes)
    db.commit()
    return RedirectResponse(
        f"/gestao/servicos?escala_id={escala_id}&ano={ano}&mes={mes}"
        "&ok=servico-removido", status_code=303)
