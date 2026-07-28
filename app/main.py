"""Ponto de entrada da aplicação FastAPI."""
import asyncio
import calendar
import logging
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app import VERSAO
from app.config import settings
from app.api import (
    auditoria_gestao, auth, calendario_gestao, escalas, escalas_gestao,
    impedimentos_gestao, militares, militares_gestao, permutas_gestao, servicos,
)
from app.database import get_db
from app.domain.calendario import classificar_dia
from app.domain.models import Cor
from app.models.escala import Escala
from app.models.militar import Militar
from app.models.servico import Servico
from app.services import calendario_service, publicacao
from app.web import ANO_MAX, ANO_MIN, templates
from app.web.gestao import NaoLogado, router as gestao_router
from app.web.gestao_auditoria import router as gestao_auditoria_router
from app.web.gestao_backup import (
    router as gestao_backup_router, router_instalacao as restaurar_instalacao_router,
)
from app.web.gestao_config import router as gestao_config_router
from app.web.gestao_escalas import router as gestao_escalas_router
from app.web.gestao_importacao import router as gestao_importacao_router
from app.web.manual import router as manual_router

BASE = Path(__file__).parent


def identificar_om(request: Request, db: Session = Depends(get_db)) -> None:
    """Põe a identificação da OM em `request.state`, para todo template ler.

    Dependência GLOBAL, e não middleware, por dois motivos: usa a mesma sessão
    do pedido (`get_db`) e obedece ao `dependency_overrides` dos testes — um
    middleware abriria uma sessão própria contra o banco em arquivo.

    Uma linha por pedido, em tabela de uma linha. Se um dia pesar, é o candidato
    óbvio a cache; hoje não paga a complexidade de invalidar.
    """
    from app.services.configuracao import identificacao, valor
    try:
        ident = identificacao(db)
        request.state.om_sigla = ident.sigla
        request.state.om_nome = ident.nome
        request.state.suporte_contato = valor(db, "suporte_contato")
    except Exception:
        # Banco ainda sem as tabelas (instalação em curso): o cabeçalho cai no
        # .env em vez de derrubar a página inteira.
        pass


async def _laco_backup_diario() -> None:
    """Mantém o backup do dia em `dados/backups` enquanto a aplicação viver.

    Roda dentro da aplicação, e não num cron do sistema, porque o deploy é um
    container só (regra 13.3): pedir ao TI da OM que configure uma tarefa
    agendada no servidor é justamente o tipo de passo que não acontece.

    `to_thread` porque a cópia é I/O bloqueante do SQLite — no laço de eventos
    ela travaria a consulta aberta durante o backup. Falha de backup **nunca**
    derruba a aplicação: escala no ar vale mais que cópia do dia, e o cartão de
    Configurações denuncia a ausência.
    """
    from app.services import backup as bkp

    while True:
        try:
            await asyncio.to_thread(bkp.gerar_automatico)
        except Exception:                                    # noqa: BLE001
            logging.getLogger(__name__).exception("falha ao gerar backup automático")
        await asyncio.sleep(bkp.INTERVALO_CHECAGEM.total_seconds())


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
    from app.services import backup as bkp

    tarefa = None
    if settings.backup_automatico and bkp.eh_arquivo():
        tarefa = asyncio.create_task(_laco_backup_diario())
    yield
    if tarefa is not None:
        tarefa.cancel()


app = FastAPI(title="Escala de Serviço", version=VERSAO,
              lifespan=ciclo_de_vida,
              dependencies=[Depends(identificar_om)])
app.mount("/static", StaticFiles(directory=BASE / "web" / "static"), name="static")

from app.services.publicacao import MESES   # fonte única dos nomes de mês

# Cabeçalho do calendário: começa no domingo (Calendar(firstweekday=6)), ordem
# diferente da de publicacao.DIAS_SEMANA, que segue date.weekday() (seg..dom).
DIAS_SEMANA = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"]

# Consulta aberta (regra 13.1).
app.include_router(militares.router)
app.include_router(escalas.router)
app.include_router(servicos.router)
# Gestão (regra 11): login e os CRUDs protegidos (auditados).
app.include_router(auth.router)
app.include_router(militares_gestao.router)
app.include_router(escalas_gestao.router)
app.include_router(calendario_gestao.router)
app.include_router(calendario_gestao.router_overrides)
app.include_router(impedimentos_gestao.router)
app.include_router(permutas_gestao.router)
app.include_router(auditoria_gestao.router)

# Telas de gestão (HTML, protegidas por cookie de sessão).
app.include_router(gestao_router)
app.include_router(gestao_escalas_router)
app.include_router(gestao_auditoria_router)
app.include_router(gestao_config_router)
app.include_router(gestao_backup_router)
# Aberta SÓ enquanto não existe gestor: é a porta da máquina nova (ver o router).
app.include_router(restaurar_instalacao_router)
app.include_router(gestao_importacao_router)
# Manual de uso: aberto, porque explica também a consulta (regra 13.1).
app.include_router(manual_router)


@app.exception_handler(NaoLogado)
async def _redireciona_login(request: Request, exc: NaoLogado):
    """Página de gestão sem sessão válida -> manda para o login (não 401 JSON)."""
    return RedirectResponse("/gestao/login", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok"}


def _mes_com_dados(db: Session, escala_id: int) -> tuple[int, int]:
    """Mês/ano do serviço mais recente da escala (default do calendário)."""
    ultimo = db.scalar(
        select(func.max(Servico.dia)).where(Servico.escala_id == escala_id)
    )
    d = ultimo or date.today()
    return d.year, d.month


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    escala_id: int | None = Query(None),
    ano: int | None = Query(None, ge=ANO_MIN, le=ANO_MAX),
    mes: int | None = Query(None, ge=1, le=12),
    db: Session = Depends(get_db),
):
    """Tela de consulta aberta (regra 13.1): calendário mensal de uma escala."""
    escalas = list(db.scalars(
        select(Escala).where(Escala.ativa.is_(True)).order_by(Escala.nome)
    ))
    if not escalas:
        return templates.TemplateResponse(request, "calendario.html", {
            "escalas": [], "escala": None,
        })

    escala = next((e for e in escalas if e.id == escala_id), escalas[0])
    if ano is None or mes is None:
        ano, mes = _mes_com_dados(db, escala.id)

    primeiro = date(ano, mes, 1)
    ultimo = date(ano, mes, calendar.monthrange(ano, mes)[1])

    # quem serve em cada dia: posto/graduação + nome de guerra. A sigla vem
    # separada porque o template a imprime em corpo menor — a célula do dia
    # comporta 12 postos (escala da guarda) e um prefixo do mesmo tamanho do
    # nome dobraria a altura de cada linha.
    servicos = db.scalars(
        select(Servico)
        .where(Servico.escala_id == escala.id, Servico.dia >= primeiro, Servico.dia <= ultimo)
        .options(joinedload(Servico.militar).joinedload(Militar.posto_graduacao))
        .order_by(Servico.dia, Servico.posto_id)
    ).all()
    por_dia: dict[date, list[dict[str, str]]] = {}
    for s in servicos:
        por_dia.setdefault(s.dia, []).append({
            "posto": s.militar.posto_graduacao.sigla,
            "nome": s.militar.nome_guerra,
        })

    # cor de cada dia (calendário: feriados + overrides)
    feriados = calendario_service.feriados(db, primeiro, ultimo)
    ov_verm = calendario_service.overrides_vermelha(db, primeiro, ultimo)
    ov_preta = calendario_service.overrides_preta(db, primeiro, ultimo)

    hoje = date.today()
    cal = calendar.Calendar(firstweekday=6)   # domingo primeiro
    semanas = []
    for semana in cal.monthdatescalendar(ano, mes):
        linha = []
        for d in semana:
            no_mes = d.month == mes
            cor = classificar_dia(d, feriados, ov_verm, ov_preta)
            linha.append({
                "dia": d.day,
                "no_mes": no_mes,
                "cor": cor.value,
                "feriado": d in feriados,
                "militares": por_dia.get(d, []) if no_mes else [],
                "hoje": d == hoje,
            })
        semanas.append(linha)

    prev = (ano - 1, 12) if mes == 1 else (ano, mes - 1)
    prox = (ano + 1, 1) if mes == 12 else (ano, mes + 1)

    return templates.TemplateResponse(request, "calendario.html", {
        "escalas": escalas,
        "escala": escala,
        "ano": ano, "mes": mes, "mes_nome": MESES[mes],
        "dias_semana": DIAS_SEMANA,
        "semanas": semanas,
        "prev": prev, "prox": prox,
    })


@app.get("/escalas/{escala_id}/impressao", response_class=HTMLResponse)
def impressao(
    escala_id: int,
    request: Request,
    ano: int | None = Query(None, ge=ANO_MIN, le=ANO_MAX),
    mes: int | None = Query(None, ge=1, le=12),
    db: Session = Depends(get_db),
):
    """Documento da escala do mês, pronto para imprimir (regra 12).

    Aberta como o resto da consulta (regra 13.1) — é o documento publicado. O
    navegador imprime ou salva em PDF; o WeasyPrint usará este mesmo template.
    """
    escala = db.scalar(
        select(Escala).options(selectinload(Escala.postos)).where(Escala.id == escala_id)
    )
    if escala is None:
        raise HTTPException(status_code=404, detail="escala não encontrada")
    if ano is None or mes is None:
        ano, mes = _mes_com_dados(db, escala.id)

    return templates.TemplateResponse(request, "impressao.html", {
        "escala": escala,
        "ano": ano, "mes": mes, "mes_nome": MESES[mes],
        "dias": publicacao.documento(db, escala, ano, mes),
        # Com um posto só, a coluna repetiria o mesmo rótulo em todas as linhas.
        "mostra_postos": len(escala.postos) > 1,
        "emitido_em": date.today(),
    })
