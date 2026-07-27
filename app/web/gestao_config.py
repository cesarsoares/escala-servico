"""Tela de CONFIGURAÇÕES da instalação (HTML, protegida — regra 11).

O sistema é instalado por qualquer OM, inclusive batalhão (regra 13.2). Tudo o
que antes exigia editar `.env`, rodar seed ou abrir o banco vive aqui:

  1. **OM da casa** — quem aparece no cabeçalho e no rodapé;
  2. **OMs** — as de origem de quem serve aqui (num QG, o efetivo vem de várias);
  3. **Postos e graduações** — a OM pode não ter uma, acrescentar outra ou usar
     nomenclatura própria. Mexer aqui muda o desempate da fila (regra 9.1);
  4. **Tipos de impedimento** (regra 7.5);
  5. **Gestores** — regra 11 fala em múltiplos; até aqui só pelo CLI
     `python -m app.seeds.usuario`.

Uma página só, com âncoras, como a tela da escala: são cinco assuntos pequenos
e correlatos, e cada um numa aba obrigaria a ir e voltar para configurar a OM.

A lógica está em `services/configuracao.py` (testável sem HTTP). Aqui só entram
leitura do formulário, auditoria e redirecionamento.
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

router = APIRouter(prefix="/gestao", tags=["web-gestão"])


def _tela(request: Request, db: Session, gestor: Usuario, erro: str | None = None,
          status: int = 200, foco: str = ""):
    """Monta a página inteira. `foco` é a âncora para onde voltar depois de gravar."""
    return templates.TemplateResponse(request, "gestao/configuracao.html", {
        "gestor": gestor,
        "erro": erro,
        "foco": foco,
        "identificacao": cfg.identificacao(db),
        "oms": list(db.scalars(select(OrganizacaoMilitar).order_by(
            OrganizacaoMilitar.propria.desc(), OrganizacaoMilitar.sigla))),
        "militares_por_om": cfg.militares_por_om(db),
        "graduacoes": db.scalars(
            select(PostoGraduacao)
            .options(joinedload(PostoGraduacao.circulo))
            .order_by(PostoGraduacao.ordem_hierarquica.desc())).all(),
        "militares_por_graduacao": cfg.militares_por_graduacao(db),
        "circulos": list(db.scalars(
            select(CirculoHierarquico).order_by(CirculoHierarquico.ordem.desc()))),
        "tipos": cfg.tipos_impedimento(db),
        "impedimentos_por_tipo": cfg.impedimentos_por_tipo(db),
        "gestores": cfg.gestores(db),
        "suporte_contato": cfg.valor(db, "suporte_contato"),
    }, status_code=status)


def _ok(chave: str, foco: str) -> RedirectResponse:
    return RedirectResponse(f"/gestao/configuracao?ok={chave}#{foco}", status_code=303)


@router.get("/configuracao", response_class=HTMLResponse)
def configuracao(request: Request, db: Session = Depends(get_db),
                 gestor: Usuario = Depends(gestor_web)):
    return _tela(request, db, gestor)


# --- 1. identificação da instalação -------------------------------------------
@router.post("/configuracao/instalacao")
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
        return _tela(request, db, gestor, str(e), status=400, foco="instalacao")
    db.commit()
    return _ok("configuracao-salva", "instalacao")


# --- 2. OMs -------------------------------------------------------------------
@router.post("/configuracao/oms")
def criar_om(request: Request, nome: str = Form(""), sigla: str = Form(""),
             db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    try:
        om = cfg.criar_om(db, nome, sigla)
    except cfg.ErroConfiguracao as e:
        return _tela(request, db, gestor, str(e), status=400, foco="oms")
    auditoria.registrar(db, usuario_id=gestor.id, entidade="organizacao_militar",
                        entidade_id=om.id, acao="criar", antes=None,
                        depois=auditoria.snapshot(om))
    db.commit()
    return _ok("om-criada", "oms")


@router.post("/configuracao/oms/{om_id}")
def alterar_om(om_id: int, request: Request, nome: str = Form(""), sigla: str = Form(""),
               db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    alvo = db.get(OrganizacaoMilitar, om_id)
    antes = auditoria.snapshot(alvo) if alvo is not None else None
    try:
        om = cfg.alterar_om(db, om_id, nome, sigla)
    except cfg.ErroConfiguracao as e:
        return _tela(request, db, gestor, str(e), status=400, foco="oms")
    auditoria.registrar(db, usuario_id=gestor.id, entidade="organizacao_militar",
                        entidade_id=om.id, acao="alterar", antes=antes,
                        depois=auditoria.snapshot(om))
    db.commit()
    return _ok("om-alterada", "oms")


@router.post("/configuracao/oms/{om_id}/excluir")
def excluir_om(om_id: int, request: Request, db: Session = Depends(get_db),
               gestor: Usuario = Depends(gestor_web)):
    alvo = db.get(OrganizacaoMilitar, om_id)
    antes = auditoria.snapshot(alvo) if alvo is not None else None
    try:
        cfg.excluir_om(db, om_id)
    except cfg.ErroConfiguracao as e:
        return _tela(request, db, gestor, str(e), status=400, foco="oms")
    auditoria.registrar(db, usuario_id=gestor.id, entidade="organizacao_militar",
                        entidade_id=om_id, acao="excluir", antes=antes, depois=None)
    db.commit()
    return _ok("om-excluida", "oms")


# --- 3. postos e graduações ---------------------------------------------------
@router.post("/configuracao/graduacoes")
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
        return _tela(request, db, gestor, str(e), status=400, foco="graduacoes")
    auditoria.registrar(db, usuario_id=gestor.id, entidade="posto_graduacao",
                        entidade_id=pg.id, acao="criar", antes=None,
                        depois=auditoria.snapshot(pg))
    db.commit()
    return _ok("graduacao-criada", "graduacoes")


@router.post("/configuracao/graduacoes/{pg_id}")
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
        return _tela(request, db, gestor, str(e), status=400, foco="graduacoes")
    auditoria.registrar(db, usuario_id=gestor.id, entidade="posto_graduacao",
                        entidade_id=pg.id, acao="alterar", antes=antes,
                        depois=auditoria.snapshot(pg))
    db.commit()
    return _ok("graduacao-alterada", "graduacoes")


@router.post("/configuracao/graduacoes/{pg_id}/mover")
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
        return _tela(request, db, gestor, str(e), status=400, foco="graduacoes")
    auditoria.registrar(db, usuario_id=gestor.id, entidade="posto_graduacao",
                        entidade_id=pg.id, acao="alterar", antes=antes,
                        depois=auditoria.snapshot(pg))
    db.commit()
    return _ok("graduacao-movida", "graduacoes")


@router.post("/configuracao/graduacoes/{pg_id}/situacao")
def situacao_graduacao(pg_id: int, request: Request, ativo: str = Form("0"),
                       db: Session = Depends(get_db),
                       gestor: Usuario = Depends(gestor_web)):
    alvo = db.get(PostoGraduacao, pg_id)
    antes = auditoria.snapshot(alvo) if alvo is not None else None
    try:
        pg = cfg.definir_graduacao_ativa(db, pg_id, ativo == "1")
    except cfg.ErroConfiguracao as e:
        return _tela(request, db, gestor, str(e), status=400, foco="graduacoes")
    auditoria.registrar(db, usuario_id=gestor.id, entidade="posto_graduacao",
                        entidade_id=pg.id, acao="alterar", antes=antes,
                        depois=auditoria.snapshot(pg))
    db.commit()
    return _ok("graduacao-ativada" if ativo == "1" else "graduacao-desativada",
               "graduacoes")


@router.post("/configuracao/graduacoes/{pg_id}/excluir")
def excluir_graduacao(pg_id: int, request: Request, db: Session = Depends(get_db),
                      gestor: Usuario = Depends(gestor_web)):
    alvo = db.get(PostoGraduacao, pg_id)
    antes = auditoria.snapshot(alvo) if alvo is not None else None
    try:
        cfg.excluir_graduacao(db, pg_id)
    except cfg.ErroConfiguracao as e:
        return _tela(request, db, gestor, str(e), status=400, foco="graduacoes")
    auditoria.registrar(db, usuario_id=gestor.id, entidade="posto_graduacao",
                        entidade_id=pg_id, acao="excluir", antes=antes, depois=None)
    db.commit()
    return _ok("graduacao-excluida", "graduacoes")


# --- 4. tipos de impedimento --------------------------------------------------
@router.post("/configuracao/tipos")
def criar_tipo(request: Request, nome: str = Form(""), db: Session = Depends(get_db),
               gestor: Usuario = Depends(gestor_web)):
    try:
        tipo = cfg.criar_tipo_impedimento(db, nome)
    except cfg.ErroConfiguracao as e:
        return _tela(request, db, gestor, str(e), status=400, foco="tipos")
    auditoria.registrar(db, usuario_id=gestor.id, entidade="tipo_impedimento",
                        entidade_id=tipo.id, acao="criar", antes=None,
                        depois=auditoria.snapshot(tipo))
    db.commit()
    return _ok("tipo-criado", "tipos")


@router.post("/configuracao/tipos/{tipo_id}/situacao")
def situacao_tipo(tipo_id: int, request: Request, ativo: str = Form("0"),
                  db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    try:
        tipo = cfg.definir_tipo_ativo(db, tipo_id, ativo == "1")
    except cfg.ErroConfiguracao as e:
        return _tela(request, db, gestor, str(e), status=400, foco="tipos")
    auditoria.registrar(db, usuario_id=gestor.id, entidade="tipo_impedimento",
                        entidade_id=tipo.id, acao="alterar", antes=None,
                        depois=auditoria.snapshot(tipo))
    db.commit()
    return _ok("tipo-ativado" if ativo == "1" else "tipo-desativado", "tipos")


@router.post("/configuracao/tipos/{tipo_id}/excluir")
def excluir_tipo(tipo_id: int, request: Request, db: Session = Depends(get_db),
                 gestor: Usuario = Depends(gestor_web)):
    try:
        cfg.excluir_tipo_impedimento(db, tipo_id)
    except cfg.ErroConfiguracao as e:
        return _tela(request, db, gestor, str(e), status=400, foco="tipos")
    auditoria.registrar(db, usuario_id=gestor.id, entidade="tipo_impedimento",
                        entidade_id=tipo_id, acao="excluir", antes=None, depois=None)
    db.commit()
    return _ok("tipo-excluido", "tipos")


# --- 5. gestores (regra 11) ---------------------------------------------------
@router.post("/configuracao/gestores")
def criar_gestor(request: Request, login: str = Form(""), nome: str = Form(""),
                 senha: str = Form(""), senha2: str = Form(""),
                 db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    try:
        novo = cfg.criar_gestor(db, login, nome, senha, senha2)
    except cfg.ErroConfiguracao as e:
        return _tela(request, db, gestor, str(e), status=400, foco="gestores")
    # snapshot() já exclui senha_hash — segredo não entra na auditoria.
    auditoria.registrar(db, usuario_id=gestor.id, entidade="usuario",
                        entidade_id=novo.id, acao="criar", antes=None,
                        depois=auditoria.snapshot(novo))
    db.commit()
    return _ok("gestor-criado", "gestores")


@router.post("/configuracao/gestores/{usuario_id}/senha")
def trocar_senha(usuario_id: int, request: Request, senha: str = Form(""),
                 senha2: str = Form(""), db: Session = Depends(get_db),
                 gestor: Usuario = Depends(gestor_web)):
    try:
        alvo = cfg.trocar_senha(db, usuario_id, senha, senha2)
    except cfg.ErroConfiguracao as e:
        return _tela(request, db, gestor, str(e), status=400, foco="gestores")
    auditoria.registrar(db, usuario_id=gestor.id, entidade="usuario",
                        entidade_id=alvo.id, acao="alterar", antes=None,
                        depois={"senha": "trocada"})
    db.commit()
    return _ok("senha-trocada", "gestores")


@router.post("/configuracao/gestores/{usuario_id}/situacao")
def situacao_gestor(usuario_id: int, request: Request, ativo: str = Form("0"),
                    db: Session = Depends(get_db),
                    gestor: Usuario = Depends(gestor_web)):
    alvo = db.get(Usuario, usuario_id)
    antes = auditoria.snapshot(alvo) if alvo is not None else None
    try:
        u = cfg.definir_gestor_ativo(db, usuario_id, ativo == "1", quem_pede=gestor.id)
    except cfg.ErroConfiguracao as e:
        return _tela(request, db, gestor, str(e), status=400, foco="gestores")
    auditoria.registrar(db, usuario_id=gestor.id, entidade="usuario",
                        entidade_id=u.id, acao="alterar", antes=antes,
                        depois=auditoria.snapshot(u))
    db.commit()
    return _ok("gestor-ativado" if ativo == "1" else "gestor-desativado", "gestores")
