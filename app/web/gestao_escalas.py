"""Telas de gestão da ESCALA e do CALENDÁRIO (HTML, protegidas — regra 11).

Fecha pela interface o que só existia na API JSON: criar/alterar/extinguir a
escala (regra 4), seus postos (2.5), participantes (3.3/7.6), a concorrência
(7.4.1) e o calendário (feriados 5.2 e overrides de dia 5.3).

Router separado de `web/gestao.py` (mesmo prefixo /gestao) só por tamanho —
espelha o corte da API entre `escalas_gestao.py` e `calendario_gestao.py`.
A sessão vem de `gestao.gestor_web`, a mesma das demais telas.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import get_db
from app.domain.models import Cor
from app.models.calendario import Feriado, OverrideDia
from app.models.escala import Escala, EscalaConcorrente, Participacao, Posto
from app.models.gestao import Usuario
from app.models.militar import Militar
from app.models.servico import Servico
from app.services import auditoria, escala_service, reajuste
from app.services import painel
from app.services.publicacao import DIAS_SEMANA, MESES
from app.web import ANO_MAX, ANO_MIN, templates
from app.web.gestao import agrupar_por_posto, gestor_web

router = APIRouter(prefix="/gestao", tags=["web-gestão"])

# Limites de sanidade dos formulários (o piso de folga é regra, os outros não).
FOLGA_PISO_HORAS = 24        # regra 7.2.1 — piso rígido
MAX_POSTOS = 60              # a maior escala real (guarda) tem ~12 vagas


# --- Escalas: lista -----------------------------------------------------------
@router.get("/escalas", response_class=HTMLResponse)
def lista_escalas(
    request: Request, extintas: int = 0,
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    stmt = select(Escala).options(selectinload(Escala.postos)).order_by(Escala.nome)
    if not extintas:
        stmt = stmt.where(Escala.ativa.is_(True))
    escalas = db.scalars(stmt).all()

    participantes = dict(db.execute(
        select(Participacao.escala_id, func.count())
        .where(Participacao.ativo.is_(True)).group_by(Participacao.escala_id)
    ).all())
    servicos = dict(db.execute(
        select(Servico.escala_id, func.count()).group_by(Servico.escala_id)
    ).all())
    return templates.TemplateResponse(request, "gestao/escalas.html", {
        "gestor": gestor, "escalas": escalas, "extintas": bool(extintas),
        "participantes": participantes, "servicos": servicos,
        # Mesma cobertura do painel: quem abre esta tela quer saber o que falta
        # fechar, e não deveria ter de ir ao painel para descobrir.
        "cobertura": painel.cobertura(db, date.today()),
        "horizonte": painel.HORIZONTE_DIAS,
    })


# --- Escalas: formulário ------------------------------------------------------
_CAMPOS_ESCALA = ("nome", "tem_preta", "tem_vermelha", "folga_minima_horas",
                  "inicio_servico", "duracao_horas", "postos")


def _v_de_escala(e: Escala) -> dict:
    return {"nome": e.nome, "tem_preta": e.tem_preta, "tem_vermelha": e.tem_vermelha,
            "folga_minima_horas": e.folga_minima_horas or "",
            "inicio_servico": e.inicio_servico.strftime("%H:%M"),
            "duracao_horas": e.duracao_horas, "postos": len(e.postos)}


def _ler_form_escala(v: dict, com_postos: bool) -> tuple[dict | None, str | None]:
    """Valida o formulário da escala. Retorna (dados, None) ou (None, erro).

    As regras aqui são as mesmas da API (4.5 ao menos uma cor, 7.2.1 piso de
    folga, 2.4 janela) — repetidas porque o form não passa pelos schemas.
    """
    nome = (v["nome"] or "").strip()
    if not nome:
        return None, "O nome da escala é obrigatório."

    tem_preta, tem_vermelha = bool(v["tem_preta"]), bool(v["tem_vermelha"])
    if not tem_preta and not tem_vermelha:
        return None, "A escala precisa rodar ao menos uma cor: preta, vermelha ou ambas (regra 4.5)."

    folga_txt = (v["folga_minima_horas"] or "").strip()
    try:
        folga = int(folga_txt) if folga_txt else None
    except ValueError:
        return None, "Folga mínima inválida."
    if folga is not None and folga < FOLGA_PISO_HORAS:
        return None, f"A folga mínima não pode ser menor que {FOLGA_PISO_HORAS}h (piso da regra 7.2.1)."

    try:
        inicio = datetime.strptime((v["inicio_servico"] or "").strip(), "%H:%M").time()
    except ValueError:
        return None, "Hora de início inválida (use HH:MM)."

    try:
        duracao = int((v["duracao_horas"] or "").strip())
    except ValueError:
        return None, "Duração inválida."
    if duracao <= 0:
        return None, "A duração do serviço precisa ser maior que zero."

    dados = {"nome": nome, "tem_preta": tem_preta, "tem_vermelha": tem_vermelha,
             "folga_minima_horas": folga, "inicio_servico": inicio,
             "duracao_horas": duracao}

    if com_postos:
        try:
            postos = int((v["postos"] or "").strip())
        except ValueError:
            return None, "Número de postos inválido."
        if not 1 <= postos <= MAX_POSTOS:
            return None, f"A escala precisa de 1 a {MAX_POSTOS} postos (regra 2.5)."
        dados["postos"] = postos
    return dados, None


def _form_escala(request, db, gestor, v, erro=None, status=200):
    return templates.TemplateResponse(request, "gestao/escala_form.html", {
        "gestor": gestor, "v": v, "erro": erro,
    }, status_code=status)


@router.get("/escalas/nova", response_class=HTMLResponse)
def escala_nova(request: Request, db: Session = Depends(get_db),
                gestor: Usuario = Depends(gestor_web)):
    return _form_escala(request, db, gestor, v={
        "tem_preta": True, "tem_vermelha": True, "inicio_servico": "08:00",
        "duracao_horas": 24, "postos": 1, "folga_minima_horas": 48,
    })


@router.post("/escalas", response_class=HTMLResponse)
def escala_criar(
    request: Request,
    nome: str = Form(""), tem_preta: str | None = Form(None),
    tem_vermelha: str | None = Form(None), folga_minima_horas: str = Form(""),
    inicio_servico: str = Form("08:00"), duracao_horas: str = Form("24"),
    postos: str = Form("1"),
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    """Cria a escala já com seus postos (regra 4.1 — escala é CRUD do gestor)."""
    v = {c: locals()[c] for c in _CAMPOS_ESCALA}
    dados, erro = _ler_form_escala(v, com_postos=True)
    if erro:
        return _form_escala(request, db, gestor, v, erro, status=400)

    quantos = dados.pop("postos")
    escala = Escala(**dados)
    escala.postos = [Posto(ordem=i) for i in range(1, quantos + 1)]
    db.add(escala)
    db.flush()
    auditoria.registrar(db, usuario_id=gestor.id, entidade="escala", entidade_id=escala.id,
                        acao="criar", depois=auditoria.snapshot(escala))
    db.commit()
    return RedirectResponse(f"/gestao/escalas/{escala.id}?ok=escala-criada", status_code=303)


# --- Escalas: detalhe (dados + postos + participantes + concorrentes) ---------
def _obter_escala(db: Session, escala_id: int) -> Escala | None:
    return db.scalar(
        select(Escala).where(Escala.id == escala_id).options(selectinload(Escala.postos))
    )


def _tela_escala(request, db, gestor, escala, erro=None, status=200, aba=None, v=None):
    """Monta a tela da escala. `v` = valores do formulário a exibir; quando o
    POST falha na validação são os que o gestor digitou, não os do banco (senão
    a correção do erro custaria redigitar tudo)."""
    postos = sorted(escala.postos, key=lambda p: p.ordem)
    com_servico = {
        pid for (pid,) in db.execute(
            select(Servico.posto_id).where(Servico.posto_id.in_([p.id for p in postos])).distinct()
        ).all()
    } if postos else set()

    vinculos = db.scalars(
        select(Participacao).where(Participacao.escala_id == escala.id)
    ).all()
    militares = {m.id: m for m in db.scalars(
        select(Militar)
        .options(joinedload(Militar.posto_graduacao), joinedload(Militar.om))
        .where(Militar.id.in_([p.militar_id for p in vinculos]))
    )} if vinculos else {}
    participantes = sorted(
        ((p, militares[p.militar_id]) for p in vinculos if p.militar_id in militares),
        key=lambda par: par[1].nome_guerra,
    )

    ja_vinculados = {p.militar_id for p in vinculos if p.ativo}
    candidatos = [m for m in db.scalars(
        select(Militar)
        .options(joinedload(Militar.posto_graduacao))
        .where(Militar.ativo.is_(True)).order_by(Militar.nome_guerra)
    ) if m.id not in ja_vinculados]

    ids_concorrentes = escala_service.concorrentes_de(db, escala.id)
    outras = db.scalars(
        select(Escala).where(Escala.id != escala.id).order_by(Escala.nome)
    ).all()
    return templates.TemplateResponse(request, "gestao/escala.html", {
        "gestor": gestor, "escala": escala, "v": v or _v_de_escala(escala), "erro": erro,
        "postos": postos, "postos_com_servico": com_servico,
        "participantes": participantes, "candidatos": agrupar_por_posto(candidatos),
        "participantes_ativos": sum(1 for p, _ in participantes if p.ativo),
        "concorrentes": [e for e in outras if e.id in ids_concorrentes],
        "disponiveis": [e for e in outras if e.id not in ids_concorrentes],
        "servicos": db.scalar(
            select(func.count()).select_from(Servico).where(Servico.escala_id == escala.id)),
        "fila": painel.fila_por_servicos(
            db, escala.id, date(date.today().year, 1, 1), date.today()),
        "ano": date.today().year,
        "aba": aba,
    }, status_code=status)


@router.get("/escalas/{escala_id}", response_class=HTMLResponse)
def escala_detalhe(escala_id: int, request: Request, db: Session = Depends(get_db),
                   gestor: Usuario = Depends(gestor_web)):
    escala = _obter_escala(db, escala_id)
    if escala is None:
        return RedirectResponse("/gestao/escalas?falha=escala-inexistente", status_code=303)
    return _tela_escala(request, db, gestor, escala)


@router.post("/escalas/{escala_id}", response_class=HTMLResponse)
def escala_atualizar(
    escala_id: int, request: Request,
    nome: str = Form(""), tem_preta: str | None = Form(None),
    tem_vermelha: str | None = Form(None), folga_minima_horas: str = Form(""),
    inicio_servico: str = Form("08:00"), duracao_horas: str = Form("24"),
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    """Altera os atributos da escala. Os postos têm seção própria."""
    escala = _obter_escala(db, escala_id)
    if escala is None:
        return RedirectResponse("/gestao/escalas?falha=escala-inexistente", status_code=303)

    v = {c: locals().get(c, "") for c in _CAMPOS_ESCALA}
    dados, erro = _ler_form_escala(v, com_postos=False)
    if erro:
        return _tela_escala(request, db, gestor, escala, erro, status=400, v=v)

    antes = auditoria.snapshot(escala)
    for campo, valor in dados.items():
        setattr(escala, campo, valor)
    db.flush()
    auditoria.registrar(db, usuario_id=gestor.id, entidade="escala", entidade_id=escala.id,
                        acao="alterar", antes=antes, depois=auditoria.snapshot(escala))
    db.commit()
    return RedirectResponse(f"/gestao/escalas/{escala.id}?ok=escala-alterada", status_code=303)


@router.post("/escalas/{escala_id}/extinguir")
def escala_extinguir(escala_id: int, db: Session = Depends(get_db),
                     gestor: Usuario = Depends(gestor_web)):
    """Extinção LÓGICA (regra 8): a escala sai da rotação, o histórico fica."""
    escala = db.get(Escala, escala_id)
    if escala is not None and escala.ativa:
        antes = auditoria.snapshot(escala)
        escala.ativa = False
        auditoria.registrar(db, usuario_id=gestor.id, entidade="escala", entidade_id=escala.id,
                            acao="excluir", antes=antes, depois=auditoria.snapshot(escala))
        db.commit()
    return RedirectResponse("/gestao/escalas?extintas=1&ok=escala-extinta", status_code=303)


@router.post("/escalas/{escala_id}/reativar")
def escala_reativar(escala_id: int, db: Session = Depends(get_db),
                    gestor: Usuario = Depends(gestor_web)):
    escala = db.get(Escala, escala_id)
    if escala is not None and not escala.ativa:
        antes = auditoria.snapshot(escala)
        escala.ativa = True
        auditoria.registrar(db, usuario_id=gestor.id, entidade="escala", entidade_id=escala.id,
                            acao="alterar", antes=antes, depois=auditoria.snapshot(escala))
        db.commit()
    return RedirectResponse(f"/gestao/escalas/{escala_id}?ok=escala-reativada", status_code=303)


# --- Postos da escala (regra 2.5) --------------------------------------------
@router.post("/escalas/{escala_id}/postos", response_class=HTMLResponse)
def posto_adicionar(
    escala_id: int, request: Request, rotulo: str = Form(""),
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    """Acrescenta uma vaga ao dia da escala. A ordem é a próxima livre."""
    escala = _obter_escala(db, escala_id)
    if escala is None:
        return RedirectResponse("/gestao/escalas?falha=escala-inexistente", status_code=303)
    if len(escala.postos) >= MAX_POSTOS:
        return _tela_escala(request, db, gestor, escala,
                            f"A escala já tem o máximo de {MAX_POSTOS} postos.",
                            status=400, aba="postos")

    proxima = max((p.ordem for p in escala.postos), default=0) + 1
    posto = Posto(escala_id=escala.id, ordem=proxima, rotulo=(rotulo.strip() or None))
    db.add(posto)
    db.flush()
    auditoria.registrar(db, usuario_id=gestor.id, entidade="posto", entidade_id=posto.id,
                        acao="criar", depois=auditoria.snapshot(posto))
    db.commit()
    return RedirectResponse(f"/gestao/escalas/{escala_id}?ok=posto-criado#postos", status_code=303)


@router.post("/escalas/{escala_id}/postos/{posto_id}", response_class=HTMLResponse)
def posto_renomear(
    escala_id: int, posto_id: int, request: Request, rotulo: str = Form(""),
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    posto = db.get(Posto, posto_id)
    if posto is not None and posto.escala_id == escala_id:
        antes = auditoria.snapshot(posto)
        posto.rotulo = rotulo.strip() or None
        db.flush()
        auditoria.registrar(db, usuario_id=gestor.id, entidade="posto", entidade_id=posto.id,
                            acao="alterar", antes=antes, depois=auditoria.snapshot(posto))
        db.commit()
    return RedirectResponse(f"/gestao/escalas/{escala_id}?ok=posto-alterado#postos", status_code=303)


@router.post("/escalas/{escala_id}/postos/{posto_id}/remover", response_class=HTMLResponse)
def posto_remover(
    escala_id: int, posto_id: int, request: Request,
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    """Remove uma vaga — só enquanto ela nunca foi usada.

    Posto com serviço gravado não sai: apagá-lo levaria junto o histórico de quem
    serviu ali (e a folga que dele decorre). Nesse caso o caminho é extinguir a
    escala (regra 8), não amputar o passado.
    """
    escala = _obter_escala(db, escala_id)
    if escala is None:
        return RedirectResponse("/gestao/escalas?falha=escala-inexistente", status_code=303)
    posto = db.get(Posto, posto_id)
    if posto is None or posto.escala_id != escala_id:
        return RedirectResponse(f"/gestao/escalas/{escala_id}?falha=posto-inexistente#postos",
                                status_code=303)

    if len(escala.postos) <= 1:
        return _tela_escala(request, db, gestor, escala,
                            "A escala precisa de pelo menos um posto (regra 2.5).",
                            status=400, aba="postos")
    usados = db.scalar(
        select(func.count()).select_from(Servico).where(Servico.posto_id == posto_id))
    if usados:
        return _tela_escala(
            request, db, gestor, escala,
            f"Este posto já tem {usados} serviço(s) gravado(s) e não pode ser removido — "
            "apagá-lo levaria o histórico junto. Para encerrar a escala, extinga-a (regra 8).",
            status=400, aba="postos")

    antes = auditoria.snapshot(posto)
    db.delete(posto)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="posto", entidade_id=posto_id,
                        acao="excluir", antes=antes)
    db.commit()
    return RedirectResponse(f"/gestao/escalas/{escala_id}?ok=posto-removido#postos",
                            status_code=303)


# --- Participantes (regra 3.3; isenção permanente = não-participação, 7.6) ----
def _cores_do_form(cores: str) -> tuple[bool, bool]:
    """Traduz o select de cores da participação (regra 3.3.1).

    Valor desconhecido cai em 'ambas', que é o padrão da regra — nunca em
    'nenhuma', que seria participar sem concorrer.
    """
    return {"preta": (True, False), "vermelha": (False, True)}.get(cores, (True, True))


@router.post("/escalas/{escala_id}/participantes", response_class=HTMLResponse)
def participante_adicionar(
    escala_id: int, request: Request, militar_id: str = Form(""),
    cores: str = Form("ambas"),
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    escala = _obter_escala(db, escala_id)
    if escala is None:
        return RedirectResponse("/gestao/escalas?falha=escala-inexistente", status_code=303)
    try:
        mid = int(militar_id)
    except ValueError:
        return _tela_escala(request, db, gestor, escala, "Selecione um militar.",
                            status=400, aba="participantes")
    if db.get(Militar, mid) is None:
        return _tela_escala(request, db, gestor, escala, "Militar inexistente.",
                            status=400, aba="participantes")

    preta, vermelha = _cores_do_form(cores)
    vinculo = db.scalar(select(Participacao).where(
        Participacao.escala_id == escala_id, Participacao.militar_id == mid))
    if vinculo is None:
        vinculo = Participacao(escala_id=escala_id, militar_id=mid, ativo=True,
                               serve_preta=preta, serve_vermelha=vermelha)
        db.add(vinculo)
        db.flush()
        auditoria.registrar(db, usuario_id=gestor.id, entidade="participacao",
                            entidade_id=vinculo.id, acao="criar",
                            depois=auditoria.snapshot(vinculo))
    elif not vinculo.ativo:
        # vínculo antigo desativado: reativar preserva o histórico e a vez na fila
        antes = auditoria.snapshot(vinculo)
        vinculo.ativo = True
        vinculo.serve_preta, vinculo.serve_vermelha = preta, vermelha
        db.flush()
        auditoria.registrar(db, usuario_id=gestor.id, entidade="participacao",
                            entidade_id=vinculo.id, acao="alterar", antes=antes,
                            depois=auditoria.snapshot(vinculo))
    # Participante novo entra pelo topo da fila (nunca serviu, regra 6.2), o que
    # muda quem serve nos dias já fechados daqui para frente (item 2, 01/08).
    rid = reajuste.registrar_auditoria(
        db, gestor_id=gestor.id, origem="participante-incluido",
        reajustes=[reajuste.reajustar(db, escala_id, date.today())])
    db.commit()
    if rid:
        return RedirectResponse(f"/gestao/reajuste/{rid}", status_code=303)
    return RedirectResponse(f"/gestao/escalas/{escala_id}?ok=participante-incluido#participantes", status_code=303)


@router.post("/escalas/{escala_id}/participantes/{militar_id}/isentar")
def participante_isentar(
    escala_id: int, militar_id: int,
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    """Isenção permanente (regra 7.6): desativa o vínculo, sem apagá-lo.

    Não é impedimento — quem é isento não guarda a vez, simplesmente deixa de
    concorrer. O vínculo fica para não perder o histórico de quem já serviu.
    """
    vinculo = db.scalar(select(Participacao).where(
        Participacao.escala_id == escala_id, Participacao.militar_id == militar_id))
    if vinculo is not None and vinculo.ativo:
        antes = auditoria.snapshot(vinculo)
        vinculo.ativo = False
        auditoria.registrar(db, usuario_id=gestor.id, entidade="participacao",
                            entidade_id=vinculo.id, acao="excluir", antes=antes,
                            depois=auditoria.snapshot(vinculo))
        rid = reajuste.registrar_auditoria(
            db, gestor_id=gestor.id, origem="participante-isento",
            reajustes=[reajuste.reajustar(db, escala_id, date.today())])
        db.commit()
        if rid:
            return RedirectResponse(f"/gestao/reajuste/{rid}", status_code=303)
    return RedirectResponse(f"/gestao/escalas/{escala_id}?ok=participante-isento#participantes", status_code=303)


@router.post("/escalas/{escala_id}/participantes/{militar_id}/cores")
def participante_cores(
    escala_id: int, militar_id: int, cores: str = Form("ambas"),
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    """Em que cores este militar concorre nesta escala (regra 3.3.1).

    Não mexe no que já está gravado: quem foi escalado num dia continua
    escalado. A restrição vale da próxima escalação em diante — como toda
    mudança de configuração da escala.
    """
    vinculo = db.scalar(select(Participacao).where(
        Participacao.escala_id == escala_id, Participacao.militar_id == militar_id))
    if vinculo is not None:
        preta, vermelha = _cores_do_form(cores)
        if (preta, vermelha) != (vinculo.serve_preta, vinculo.serve_vermelha):
            antes = auditoria.snapshot(vinculo)
            vinculo.serve_preta, vinculo.serve_vermelha = preta, vermelha
            db.flush()
            auditoria.registrar(db, usuario_id=gestor.id, entidade="participacao",
                                entidade_id=vinculo.id, acao="alterar", antes=antes,
                                depois=auditoria.snapshot(vinculo))
            db.commit()
    return RedirectResponse(f"/gestao/escalas/{escala_id}?ok=participante-cores#participantes",
                            status_code=303)


# --- Concorrência entre escalas (regra 7.4.1) --------------------------------
@router.post("/escalas/{escala_id}/concorrentes", response_class=HTMLResponse)
def concorrente_declarar(
    escala_id: int, request: Request, outra_id: str = Form(""),
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    """Declara a concorrência (simétrica): vale nos dois sentidos, de uma vez."""
    escala = _obter_escala(db, escala_id)
    if escala is None:
        return RedirectResponse("/gestao/escalas?falha=escala-inexistente", status_code=303)
    try:
        outra = int(outra_id)
    except ValueError:
        return _tela_escala(request, db, gestor, escala, "Selecione a outra escala.",
                            status=400, aba="concorrentes")

    par = tuple(sorted((escala_id, outra)))
    ja_existia = db.get(EscalaConcorrente, par) is not None
    try:
        vinculo = escala_service.declarar_concorrencia(db, escala_id, outra)
    except ValueError as e:
        return _tela_escala(request, db, gestor, escala, str(e), status=400,
                            aba="concorrentes")
    if not ja_existia:
        auditoria.registrar(db, usuario_id=gestor.id, entidade="escala_concorrente",
                            entidade_id=escala_id, acao="criar",
                            depois=auditoria.snapshot(vinculo))
    db.commit()
    return RedirectResponse(f"/gestao/escalas/{escala_id}?ok=concorrencia-criada#concorrentes", status_code=303)


@router.post("/escalas/{escala_id}/concorrentes/{outra_id}/remover")
def concorrente_remover(
    escala_id: int, outra_id: int,
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    if escala_service.remover_concorrencia(db, escala_id, outra_id):
        auditoria.registrar(db, usuario_id=gestor.id, entidade="escala_concorrente",
                            entidade_id=escala_id, acao="excluir",
                            antes={"escala_menor_id": min(escala_id, outra_id),
                                   "escala_maior_id": max(escala_id, outra_id)})
        db.commit()
    return RedirectResponse(f"/gestao/escalas/{escala_id}?ok=concorrencia-removida#concorrentes", status_code=303)


# --- Calendário: feriados (5.2) e overrides de dia (5.3) ---------------------
def _tela_calendario(request, db, gestor, ano, erro=None, status=200):
    primeiro, ultimo = date(ano, 1, 1), date(ano, 12, 31)
    feriados = db.scalars(
        select(Feriado).where(Feriado.data >= primeiro, Feriado.data <= ultimo)
        .order_by(Feriado.data)
    ).all()
    overrides = db.scalars(
        select(OverrideDia).where(OverrideDia.data >= primeiro, OverrideDia.data <= ultimo)
        .order_by(OverrideDia.data)
    ).all()
    return templates.TemplateResponse(request, "gestao/calendario.html", {
        "gestor": gestor, "ano": ano, "erro": erro, "meses": MESES,
        # O dia da semana é o que importa aqui: feriado no sábado não muda nada
        # (já era vermelha), feriado na quarta vira uma vermelha no meio da fila.
        "dias_semana": DIAS_SEMANA,
        "feriados": feriados, "overrides": overrides,
    }, status_code=status)


@router.get("/calendario", response_class=HTMLResponse)
def calendario(
    request: Request, ano: int | None = Query(None, ge=ANO_MIN, le=ANO_MAX),
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    return _tela_calendario(request, db, gestor, ano or date.today().year)


def _data_do_form(texto: str) -> date | None:
    try:
        return date.fromisoformat((texto or "").strip())
    except ValueError:
        return None


@router.post("/calendario/feriados", response_class=HTMLResponse)
def feriado_criar(
    request: Request, data: str = Form(""), nome: str = Form(""),
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    """Feriado adicionado pelo gestor (regra 5.2) — os nacionais vêm no seed."""
    ano = date.today().year
    dia = _data_do_form(data)
    if dia is None:
        return _tela_calendario(request, db, gestor, ano, "Data inválida.", status=400)
    if ANO_MIN > dia.year or dia.year > ANO_MAX:
        return _tela_calendario(request, db, gestor, ano, "Data fora da faixa.", status=400)
    if not nome.strip():
        return _tela_calendario(request, db, gestor, dia.year,
                                "Informe o nome do feriado.", status=400)

    feriado = Feriado(data=dia, nome=nome.strip(), nacional=False)
    db.add(feriado)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return _tela_calendario(request, db, gestor, dia.year,
                                "Já existe feriado nesta data.", status=409)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="feriado", entidade_id=feriado.id,
                        acao="criar", depois=auditoria.snapshot(feriado))
    db.commit()
    return RedirectResponse(f"/gestao/calendario?ano={dia.year}&ok=feriado-criado", status_code=303)


@router.post("/calendario/feriados/{feriado_id}/remover")
def feriado_remover(
    feriado_id: int, db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    """Remove o feriado. Não recalcula o que já foi escalado (regra 10)."""
    feriado = db.get(Feriado, feriado_id)
    ano = feriado.data.year if feriado is not None else date.today().year
    if feriado is not None:
        antes = auditoria.snapshot(feriado)
        db.delete(feriado)
        auditoria.registrar(db, usuario_id=gestor.id, entidade="feriado",
                            entidade_id=feriado_id, acao="excluir", antes=antes)
        db.commit()
    return RedirectResponse(f"/gestao/calendario?ano={ano}&ok=feriado-removido", status_code=303)


@router.post("/calendario/overrides", response_class=HTMLResponse)
def override_definir(
    request: Request, data: str = Form(""), cor: str = Form(""),
    observacao: str = Form(""),
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    """Força a cor de um dia (regra 5.3). Upsert pela data, com observação.

    Vale nos dois sentidos: dia útil que vira vermelha (o caso da regra) e
    feriado/fim de semana trabalhado que vira preta.
    """
    ano = date.today().year
    dia = _data_do_form(data)
    if dia is None:
        return _tela_calendario(request, db, gestor, ano, "Data inválida.", status=400)
    if ANO_MIN > dia.year or dia.year > ANO_MAX:
        return _tela_calendario(request, db, gestor, ano, "Data fora da faixa.", status=400)
    try:
        cor_escolhida = Cor(cor)
    except ValueError:
        return _tela_calendario(request, db, gestor, dia.year, "Cor inválida.", status=400)

    override = db.get(OverrideDia, dia)
    obs = observacao.strip() or None
    if override is None:
        override = OverrideDia(data=dia, cor=cor_escolhida, observacao=obs)
        db.add(override)
        db.flush()
        auditoria.registrar(db, usuario_id=gestor.id, entidade="override_dia",
                            acao="criar", depois=auditoria.snapshot(override))
    else:
        antes = auditoria.snapshot(override)
        override.cor, override.observacao = cor_escolhida, obs
        db.flush()
        auditoria.registrar(db, usuario_id=gestor.id, entidade="override_dia",
                            acao="alterar", antes=antes, depois=auditoria.snapshot(override))
    db.commit()
    return RedirectResponse(f"/gestao/calendario?ano={dia.year}&ok=override-definido", status_code=303)


@router.post("/calendario/overrides/{dia}/remover")
def override_remover(
    dia: date, db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    """Tira o override: o dia volta à cor natural (regra 5.1)."""
    override = db.get(OverrideDia, dia)
    if override is not None:
        antes = auditoria.snapshot(override)
        db.delete(override)
        auditoria.registrar(db, usuario_id=gestor.id, entidade="override_dia",
                            acao="excluir", antes=antes)
        db.commit()
    return RedirectResponse(f"/gestao/calendario?ano={dia.year}&ok=override-removido", status_code=303)
