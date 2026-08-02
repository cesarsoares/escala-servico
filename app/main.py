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
from app.models.servico import Permuta, Servico
from app.services import calendario_service, publicacao
from app.web import ANO_MAX, ANO_MIN, templates
from app.web.gestao import NaoLogado, router as gestao_router
from app.web.gestao_auditoria import router as gestao_auditoria_router
from app.web.gestao_backup import (
    router as gestao_backup_router, router_instalacao as restaurar_instalacao_router,
)
from app.web.gestao_config import router as gestao_config_router
from app.web.gestao_conflitos import router as gestao_conflitos_router
from app.web.gestao_escalas import router as gestao_escalas_router
from app.web.gestao_importacao import router as gestao_importacao_router
from app.web.gestao_lancamento import router as gestao_lancamento_router
from app.web.gestao_reajuste import router as gestao_reajuste_router
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
app.include_router(gestao_conflitos_router)
app.include_router(gestao_lancamento_router)
app.include_router(gestao_reajuste_router)
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

    # Permutas do mês. Quem lê a consulta quer saber quem VAI ao serviço, então
    # o template põe o substituto primeiro e o escalado logo abaixo — mas os
    # dois aparecem: a folga continua sendo do escalado (regra 9), e sumir com o
    # nome dele faria o documento mentir sobre de quem é o serviço.
    # `Permuta` não tem relação para o militar; carrego à parte, como publicacao.
    ids = [s.id for s in servicos]
    permutas = {
        p.servico_id: p.militar_substituto_id
        for p in (db.scalars(select(Permuta).where(Permuta.servico_id.in_(ids))) if ids else [])
    }
    substitutos = {
        m.id: m for m in (db.scalars(
            select(Militar)
            .options(joinedload(Militar.posto_graduacao))
            .where(Militar.id.in_(set(permutas.values())))
        ) if permutas else [])
    }

    por_dia: dict[date, list[dict[str, str | None]]] = {}
    for s in servicos:
        sub = substitutos.get(permutas.get(s.id))
        por_dia.setdefault(s.dia, []).append({
            "posto": s.militar.posto_graduacao.sigla,
            "nome": s.militar.nome_guerra,
            "sub_posto": sub.posto_graduacao.sigla if sub else None,
            "sub_nome": sub.nome_guerra if sub else None,
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


# Teto do período de impressão. A página é ABERTA (regra 13.1): sem teto,
# `?inicio=1900-01-01&fim=2200-12-31` monta uma tabela de 110 mil linhas a cada
# pedido. Um ano cobre com folga o uso real (o brigada imprime de 15 em 15 dias).
MAX_DIAS_IMPRESSAO = 366


def _data_da_url(texto: str | None) -> date | None:
    """Data ISO vinda da querystring; ilegível ou fora da faixa vira None.

    A URL é aberta e editável à mão — data inválida tem de virar mensagem na
    tela (o motivo da recusa), nunca 500 nem 422 em branco.
    """
    try:
        d = date.fromisoformat((texto or "").strip())
    except ValueError:
        return None
    return d if ANO_MIN <= d.year <= ANO_MAX else None


@app.get("/escalas/{escala_id}/impressao", response_class=HTMLResponse)
def impressao(
    escala_id: int,
    request: Request,
    ano: int | None = Query(None, ge=ANO_MIN, le=ANO_MAX),
    mes: int | None = Query(None, ge=1, le=12),
    inicio: str | None = Query(None),
    fim: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Documento da escala, pronto para imprimir (regra 12).

    Aberta como o resto da consulta (regra 13.1) — é o documento publicado. O
    navegador imprime ou salva em PDF; o WeasyPrint usará este mesmo template.

    Dois jeitos de dizer o período: `?ano=&mes=` (o mês cheio, que é o default e
    o que o calendário liga) ou `?inicio=&fim=` em datas ISO, que é o intervalo
    livre — a previsão de 15/ago a 15/set não cabia num mês. O intervalo tem
    precedência; período recusado explica o motivo e cai no mês.
    """
    escala = db.scalar(
        select(Escala).options(selectinload(Escala.postos)).where(Escala.id == escala_id)
    )
    if escala is None:
        raise HTTPException(status_code=404, detail="escala não encontrada")

    d_ini, d_fim = _data_da_url(inicio), _data_da_url(fim)
    erro_periodo = None
    if inicio or fim:
        if d_ini is None or d_fim is None:
            erro_periodo = ("Informe as duas datas do período (inicial e final) "
                            f"em anos entre {ANO_MIN} e {ANO_MAX}.")
        elif d_fim < d_ini:
            erro_periodo = "A data final é anterior à inicial."
        elif (d_fim - d_ini).days + 1 > MAX_DIAS_IMPRESSAO:
            erro_periodo = (f"O período pedido passa de {MAX_DIAS_IMPRESSAO} dias. "
                            "Imprima em partes.")

    if erro_periodo is None and d_ini and d_fim:
        p_ini, p_fim = d_ini, d_fim
    else:
        if ano is None or mes is None:
            ano, mes = _mes_com_dados(db, escala.id)
        p_ini, p_fim = publicacao.periodo_do_mes(ano, mes)

    return templates.TemplateResponse(request, "impressao.html", {
        "escala": escala,
        # Voltar ao calendário: ele é mensal, então cai no mês em que o período começa.
        "ano": p_ini.year, "mes": p_ini.month,
        # Campos do formulário: recusado o período, voltam com o que foi
        # DIGITADO (e não com o mês em que o documento caiu), senão o gestor
        # precisa redigitar as duas datas para corrigir uma.
        "campo_inicio": (inicio or "") if erro_periodo else p_ini.isoformat(),
        "campo_fim": (fim or "") if erro_periodo else p_fim.isoformat(),
        "periodo": publicacao.rotulo_periodo(p_ini, p_fim),
        "erro_periodo": erro_periodo,
        "dias": publicacao.documento(db, escala, p_ini, p_fim),
        # Período que atravessa a virada do mês: a coluna do dia precisa do mês
        # junto, senão "15" aparece duas vezes sem dizer qual é qual.
        "mostra_mes": (p_ini.year, p_ini.month) != (p_fim.year, p_fim.month),
        # Com um posto só, a coluna repetiria o mesmo rótulo em todas as linhas.
        "mostra_postos": len(escala.postos) > 1,
        "emitido_em": date.today(),
    })
