"""Telas de CONFIGURAÇÃO da instalação (HTML, protegidas — regra 11).

O sistema é instalado por qualquer OM, inclusive batalhão (regra 13.2). Tudo o
que antes exigia editar `.env`, rodar seed ou abrir o banco vive aqui:

  1. **Esta instalação** — qual é a OM da casa e o contato do suporte;
  2. **OMs** — as de origem de quem serve aqui (num QG, o efetivo vem de várias);
  3. **Postos e graduações** — a OM pode não ter um, acrescentar outro ou usar
     nomenclatura própria. Mexer aqui muda o desempate da fila (regra 9.1);
  4. **Tipos de impedimento** (regra 7.5);
  5. **Gestores** — regra 11 fala em múltiplos; antes só pelo CLI.

`/gestao/configuracao` é um **hub de cartões** e cada assunto tem a sua página.
A primeira versão era uma página só com âncoras, e não se sustentou: a seção de
graduações tem 17 linhas com formulário embutido em cada uma, e enterrava as
outras quatro. Cada cartão traz a contagem **e a pendência** — um hub que só
repete títulos custa um clique e não devolve nada.

A lógica está em `services/configuracao.py` (testável sem HTTP). Aqui só entram
leitura de formulário, auditoria e redirecionamento.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.gestao import Usuario
from app.models.referencia import CirculoHierarquico, OrganizacaoMilitar, PostoGraduacao
from app.services import auditoria
from app.services import configuracao as cfg
from app.web import templates
from app.web.gestao import gestor_web

router = APIRouter(prefix="/gestao/configuracao", tags=["web-gestão"])


def _cartao(db: Session, chave: str) -> cfg.Cartao | None:
    """Título, descrição e pendência da seção — os MESMOS que o hub mostra.

    A página de cada seção reusa o cartão em vez de repetir o texto: fonte
    única, e o gestor reencontra no cabeçalho a frase em que clicou.
    """
    return next((c for c in cfg.panorama(db) if c.chave == chave), None)


def _pagina(request: Request, db: Session, gestor: Usuario, secao: str,
            extra: dict | None = None, erro: str | None = None, status: int = 200):
    """Renderiza a página de uma seção, com o cabeçalho vindo do panorama."""
    contexto = {
        "gestor": gestor,
        "erro": erro,
        "cartao": _cartao(db, secao),
        "secao": secao,
    }
    contexto.update(extra or {})
    return templates.TemplateResponse(
        request, f"gestao/config/{secao}.html", contexto, status_code=status)


def _ok(secao: str, chave: str) -> RedirectResponse:
    """Volta para a página da própria seção — não para o hub. Quem acabou de
    cadastrar uma OM normalmente vai cadastrar a próxima."""
    return RedirectResponse(f"/gestao/configuracao/{secao}?ok={chave}", status_code=303)


# --- hub ----------------------------------------------------------------------
@router.get("", response_class=HTMLResponse)
def configuracao(request: Request, db: Session = Depends(get_db),
                 gestor: Usuario = Depends(gestor_web)):
    return templates.TemplateResponse(request, "gestao/config/hub.html", {
        "gestor": gestor,
        "cartoes": cfg.panorama(db),
    })


# --- 1. identificação da instalação -------------------------------------------
def _tela_instalacao(request, db, gestor, erro=None, status=200):
    return _pagina(request, db, gestor, "instalacao", {
        "identificacao": cfg.identificacao(db),
        "oms": list(db.scalars(select(OrganizacaoMilitar).order_by(
            OrganizacaoMilitar.propria.desc(), OrganizacaoMilitar.sigla))),
        "suporte": cfg.valor(db, "suporte_contato"),
    }, erro, status)


@router.get("/instalacao", response_class=HTMLResponse)
def instalacao(request: Request, db: Session = Depends(get_db),
               gestor: Usuario = Depends(gestor_web)):
    return _tela_instalacao(request, db, gestor)


@router.post("/instalacao")
def definir_instalacao(request: Request, om_id: str = Form(""),
                       suporte_contato: str = Form(""),
                       db: Session = Depends(get_db),
                       gestor: Usuario = Depends(gestor_web)):
    """Marca a OM da casa e grava o contato do suporte (rodapé)."""
    try:
        if om_id.strip().isdigit():
            om = cfg.definir_om_propria(db, int(om_id))
            auditoria.registrar(db, usuario_id=gestor.id, entidade="configuracao",
                                entidade_id=om.id, acao="alterar", antes=None,
                                depois={"om_propria": om.sigla})
        cfg.definir(db, "suporte_contato", suporte_contato)
    except cfg.ErroConfiguracao as e:
        return _tela_instalacao(request, db, gestor, str(e), status=400)
    db.commit()
    return _ok("instalacao", "configuracao-salva")


# --- 2. OMs -------------------------------------------------------------------
def _tela_oms(request, db, gestor, erro=None, status=200):
    return _pagina(request, db, gestor, "oms", {
        "oms": list(db.scalars(select(OrganizacaoMilitar).order_by(
            OrganizacaoMilitar.propria.desc(), OrganizacaoMilitar.sigla))),
        "militares_por_om": cfg.militares_por_om(db),
    }, erro, status)


@router.get("/oms", response_class=HTMLResponse)
def oms(request: Request, db: Session = Depends(get_db),
        gestor: Usuario = Depends(gestor_web)):
    return _tela_oms(request, db, gestor)


@router.post("/oms")
def criar_om(request: Request, nome: str = Form(""), sigla: str = Form(""),
             db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    try:
        om = cfg.criar_om(db, nome, sigla)
    except cfg.ErroConfiguracao as e:
        return _tela_oms(request, db, gestor, str(e), status=400)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="organizacao_militar",
                        entidade_id=om.id, acao="criar", antes=None,
                        depois=auditoria.snapshot(om))
    db.commit()
    return _ok("oms", "om-criada")


@router.post("/oms/{om_id}")
def alterar_om(om_id: int, request: Request, nome: str = Form(""), sigla: str = Form(""),
               db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    alvo = db.get(OrganizacaoMilitar, om_id)
    antes = auditoria.snapshot(alvo) if alvo is not None else None
    try:
        om = cfg.alterar_om(db, om_id, nome, sigla)
    except cfg.ErroConfiguracao as e:
        return _tela_oms(request, db, gestor, str(e), status=400)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="organizacao_militar",
                        entidade_id=om.id, acao="alterar", antes=antes,
                        depois=auditoria.snapshot(om))
    db.commit()
    return _ok("oms", "om-alterada")


@router.post("/oms/{om_id}/excluir")
def excluir_om(om_id: int, request: Request, db: Session = Depends(get_db),
               gestor: Usuario = Depends(gestor_web)):
    alvo = db.get(OrganizacaoMilitar, om_id)
    antes = auditoria.snapshot(alvo) if alvo is not None else None
    try:
        cfg.excluir_om(db, om_id)
    except cfg.ErroConfiguracao as e:
        return _tela_oms(request, db, gestor, str(e), status=400)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="organizacao_militar",
                        entidade_id=om_id, acao="excluir", antes=antes, depois=None)
    db.commit()
    return _ok("oms", "om-excluida")


# --- 3. postos e graduações ---------------------------------------------------
def _tela_graduacoes(request, db, gestor, erro=None, status=200):
    return _pagina(request, db, gestor, "graduacoes", {
        "graduacoes": db.scalars(
            select(PostoGraduacao)
            .options(joinedload(PostoGraduacao.circulo))
            .order_by(PostoGraduacao.ordem_hierarquica.desc())).all(),
        "militares_por_graduacao": cfg.militares_por_graduacao(db),
        "circulos": list(db.scalars(
            select(CirculoHierarquico).order_by(CirculoHierarquico.ordem.desc()))),
    }, erro, status)


@router.get("/graduacoes", response_class=HTMLResponse)
def graduacoes(request: Request, db: Session = Depends(get_db),
               gestor: Usuario = Depends(gestor_web)):
    return _tela_graduacoes(request, db, gestor)


@router.post("/graduacoes")
def criar_graduacao(request: Request, sigla: str = Form(""), nome: str = Form(""),
                    circulo_id: str = Form(""), abaixo_de_id: str = Form(""),
                    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    try:
        if not circulo_id.strip().isdigit():
            raise cfg.ErroConfiguracao("Escolha o círculo hierárquico.")
        pg = cfg.criar_graduacao(
            db, sigla, nome, int(circulo_id),
            int(abaixo_de_id) if abaixo_de_id.strip().isdigit() else None)
    except cfg.ErroConfiguracao as e:
        return _tela_graduacoes(request, db, gestor, str(e), status=400)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="posto_graduacao",
                        entidade_id=pg.id, acao="criar", antes=None,
                        depois=auditoria.snapshot(pg))
    db.commit()
    return _ok("graduacoes", "graduacao-criada")


@router.post("/graduacoes/{pg_id}")
def alterar_graduacao(pg_id: int, request: Request, sigla: str = Form(""),
                      nome: str = Form(""), circulo_id: str = Form(""),
                      db: Session = Depends(get_db),
                      gestor: Usuario = Depends(gestor_web)):
    alvo = db.get(PostoGraduacao, pg_id)
    antes = auditoria.snapshot(alvo) if alvo is not None else None
    try:
        if not circulo_id.strip().isdigit():
            raise cfg.ErroConfiguracao("Escolha o círculo hierárquico.")
        pg = cfg.alterar_graduacao(db, pg_id, sigla, nome, int(circulo_id))
    except cfg.ErroConfiguracao as e:
        return _tela_graduacoes(request, db, gestor, str(e), status=400)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="posto_graduacao",
                        entidade_id=pg.id, acao="alterar", antes=antes,
                        depois=auditoria.snapshot(pg))
    db.commit()
    return _ok("graduacoes", "graduacao-alterada")


@router.post("/graduacoes/{pg_id}/mover")
def mover_graduacao(pg_id: int, request: Request, direcao: str = Form(""),
                    db: Session = Depends(get_db),
                    gestor: Usuario = Depends(gestor_web)):
    """Sobe/desce na hierarquia. Vale da próxima escalação em diante — os
    serviços já gravados não mudam (regra 9.1)."""
    alvo = db.get(PostoGraduacao, pg_id)
    antes = auditoria.snapshot(alvo) if alvo is not None else None
    try:
        pg = cfg.mover_graduacao(db, pg_id, direcao)
    except cfg.ErroConfiguracao as e:
        return _tela_graduacoes(request, db, gestor, str(e), status=400)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="posto_graduacao",
                        entidade_id=pg.id, acao="alterar", antes=antes,
                        depois=auditoria.snapshot(pg))
    db.commit()
    return _ok("graduacoes", "graduacao-movida")


@router.post("/graduacoes/{pg_id}/situacao")
def situacao_graduacao(pg_id: int, request: Request, ativo: str = Form("0"),
                       db: Session = Depends(get_db),
                       gestor: Usuario = Depends(gestor_web)):
    alvo = db.get(PostoGraduacao, pg_id)
    antes = auditoria.snapshot(alvo) if alvo is not None else None
    try:
        pg = cfg.definir_graduacao_ativa(db, pg_id, ativo == "1")
    except cfg.ErroConfiguracao as e:
        return _tela_graduacoes(request, db, gestor, str(e), status=400)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="posto_graduacao",
                        entidade_id=pg.id, acao="alterar", antes=antes,
                        depois=auditoria.snapshot(pg))
    db.commit()
    return _ok("graduacoes",
               "graduacao-ativada" if ativo == "1" else "graduacao-desativada")


@router.post("/graduacoes/{pg_id}/excluir")
def excluir_graduacao(pg_id: int, request: Request, db: Session = Depends(get_db),
                      gestor: Usuario = Depends(gestor_web)):
    alvo = db.get(PostoGraduacao, pg_id)
    antes = auditoria.snapshot(alvo) if alvo is not None else None
    try:
        cfg.excluir_graduacao(db, pg_id)
    except cfg.ErroConfiguracao as e:
        return _tela_graduacoes(request, db, gestor, str(e), status=400)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="posto_graduacao",
                        entidade_id=pg_id, acao="excluir", antes=antes, depois=None)
    db.commit()
    return _ok("graduacoes", "graduacao-excluida")


# --- 4. tipos de impedimento --------------------------------------------------
def _tela_tipos(request, db, gestor, erro=None, status=200):
    return _pagina(request, db, gestor, "tipos", {
        "tipos": cfg.tipos_impedimento(db),
        "impedimentos_por_tipo": cfg.impedimentos_por_tipo(db),
    }, erro, status)


@router.get("/tipos", response_class=HTMLResponse)
def tipos(request: Request, db: Session = Depends(get_db),
          gestor: Usuario = Depends(gestor_web)):
    return _tela_tipos(request, db, gestor)


@router.post("/tipos")
def criar_tipo(request: Request, nome: str = Form(""), db: Session = Depends(get_db),
               gestor: Usuario = Depends(gestor_web)):
    try:
        tipo = cfg.criar_tipo_impedimento(db, nome)
    except cfg.ErroConfiguracao as e:
        return _tela_tipos(request, db, gestor, str(e), status=400)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="tipo_impedimento",
                        entidade_id=tipo.id, acao="criar", antes=None,
                        depois=auditoria.snapshot(tipo))
    db.commit()
    return _ok("tipos", "tipo-criado")


@router.post("/tipos/{tipo_id}/situacao")
def situacao_tipo(tipo_id: int, request: Request, ativo: str = Form("0"),
                  db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    try:
        tipo = cfg.definir_tipo_ativo(db, tipo_id, ativo == "1")
    except cfg.ErroConfiguracao as e:
        return _tela_tipos(request, db, gestor, str(e), status=400)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="tipo_impedimento",
                        entidade_id=tipo.id, acao="alterar", antes=None,
                        depois=auditoria.snapshot(tipo))
    db.commit()
    return _ok("tipos", "tipo-ativado" if ativo == "1" else "tipo-desativado")


@router.post("/tipos/{tipo_id}/excluir")
def excluir_tipo(tipo_id: int, request: Request, db: Session = Depends(get_db),
                 gestor: Usuario = Depends(gestor_web)):
    try:
        cfg.excluir_tipo_impedimento(db, tipo_id)
    except cfg.ErroConfiguracao as e:
        return _tela_tipos(request, db, gestor, str(e), status=400)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="tipo_impedimento",
                        entidade_id=tipo_id, acao="excluir", antes=None, depois=None)
    db.commit()
    return _ok("tipos", "tipo-excluido")


# --- 5. gestores (regra 11) ---------------------------------------------------
def _tela_gestores(request, db, gestor, erro=None, status=200):
    return _pagina(request, db, gestor, "gestores", {
        "gestores": cfg.gestores(db),
        "senha_minima": cfg.SENHA_MINIMA,
    }, erro, status)


@router.get("/gestores", response_class=HTMLResponse)
def gestores(request: Request, db: Session = Depends(get_db),
             gestor: Usuario = Depends(gestor_web)):
    return _tela_gestores(request, db, gestor)


@router.post("/gestores")
def criar_gestor(request: Request, login: str = Form(""), nome: str = Form(""),
                 senha: str = Form(""), senha2: str = Form(""),
                 db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    try:
        novo = cfg.criar_gestor(db, login, nome, senha, senha2)
    except cfg.ErroConfiguracao as e:
        return _tela_gestores(request, db, gestor, str(e), status=400)
    # snapshot() já exclui senha_hash — segredo não entra na auditoria.
    auditoria.registrar(db, usuario_id=gestor.id, entidade="usuario",
                        entidade_id=novo.id, acao="criar", antes=None,
                        depois=auditoria.snapshot(novo))
    db.commit()
    return _ok("gestores", "gestor-criado")


@router.post("/gestores/{usuario_id}/senha")
def trocar_senha(usuario_id: int, request: Request, senha: str = Form(""),
                 senha2: str = Form(""), db: Session = Depends(get_db),
                 gestor: Usuario = Depends(gestor_web)):
    try:
        alvo = cfg.trocar_senha(db, usuario_id, senha, senha2)
    except cfg.ErroConfiguracao as e:
        return _tela_gestores(request, db, gestor, str(e), status=400)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="usuario",
                        entidade_id=alvo.id, acao="alterar", antes=None,
                        depois={"senha": "trocada"})
    db.commit()
    return _ok("gestores", "senha-trocada")


@router.post("/gestores/{usuario_id}/situacao")
def situacao_gestor(usuario_id: int, request: Request, ativo: str = Form("0"),
                    db: Session = Depends(get_db),
                    gestor: Usuario = Depends(gestor_web)):
    alvo = db.get(Usuario, usuario_id)
    antes = auditoria.snapshot(alvo) if alvo is not None else None
    try:
        u = cfg.definir_gestor_ativo(db, usuario_id, ativo == "1", quem_pede=gestor.id)
    except cfg.ErroConfiguracao as e:
        return _tela_gestores(request, db, gestor, str(e), status=400)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="usuario",
                        entidade_id=u.id, acao="alterar", antes=antes,
                        depois=auditoria.snapshot(u))
    db.commit()
    return _ok("gestores", "gestor-ativado" if ativo == "1" else "gestor-desativado")
