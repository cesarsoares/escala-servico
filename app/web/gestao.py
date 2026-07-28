"""Telas de gestão (HTML, protegidas por cookie de sessão — regra 11).

A consulta é aberta (regra 13.1); estas páginas exigem login. O cookie
`access_token` é o mesmo JWT da API — `get_current_user` (JSON) e `gestor_web`
(HTML) leem os dois. Sem sessão, `gestor_web` levanta `NaoLogado`, que o main
converte em redirecionamento para o login (em vez de 401 JSON).
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

import jwt
from fastapi import (
    APIRouter, Cookie, Depends, File, Form, HTTPException, Query, Request, UploadFile,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.militares_gestao import MAX_FICHA_BYTES, _validar_fks_e_antiguidade, ler_ficha
from app.config import settings
from app.database import get_db
from app.models.escala import Escala, Participacao
from app.models.gestao import Auditoria, Usuario
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao, TipoImpedimento
from app.models.servico import Permuta, Servico
from app.normalizacao import so_digitos
from app.security import criar_token, hash_dummy, ler_token, verificar_senha
from app.services import auditoria, importacao, rotacao
from app.services import configuracao as cfg
from app.services import instalacao
from app.services import painel as painel_service
from app.services import permuta as permuta_service
from app.services.publicacao import DIAS_SEMANA, MESES
from app.web import ANO_MAX, ANO_MIN, templates

router = APIRouter(prefix="/gestao", tags=["web-gestão"])

MAX_DIAS = 366


class NaoLogado(Exception):
    """Acesso a página de gestão sem sessão válida (tratado no main → login)."""


def gestor_web(
    access_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> Usuario:
    """Gestor logado a partir do cookie; levanta NaoLogado se ausente/inválido."""
    if not access_token:
        raise NaoLogado()
    try:
        uid = ler_token(access_token)
    except jwt.PyJWTError:
        raise NaoLogado()
    usuario = db.get(Usuario, uid)
    if usuario is None or not usuario.ativo:
        raise NaoLogado()
    return usuario


# --- Primeiro acesso (regra 11) ---
# Instalação recém-subida não tem gestor nenhum, e a gestão exige login: sem
# esta tela, o único caminho para entrar seria a linha de comando dentro do
# container (`python -m app.seeds.usuario`). Para a seção de TI que recebeu só
# a imagem, isso é uma parede na porta. O CLI continua existindo — é o socorro
# para senha perdida, que a tela (fechada assim que há gestor) não resolve.
@router.get("/primeiro-acesso", response_class=HTMLResponse)
def primeiro_acesso_form(request: Request, db: Session = Depends(get_db),
                         erro: str | None = None, v: dict | None = None):
    if not instalacao.sem_gestor(db):
        return RedirectResponse("/gestao/login", status_code=303)
    return templates.TemplateResponse(request, "gestao/primeiro_acesso.html", {
        "erro": erro, "v": v or {}, "senha_minima": cfg.SENHA_MINIMA,
    })


@router.post("/primeiro-acesso", response_class=HTMLResponse)
def primeiro_acesso(
    request: Request, login: str = Form(""), nome: str = Form(""),
    senha: str = Form(""), senha2: str = Form(""),
    db: Session = Depends(get_db),
):
    """Cria o PRIMEIRO gestor e já o deixa logado.

    A checagem de "não existe gestor" é refeita aqui, e não só no GET: entre
    abrir a tela e enviá-la, alguém pode ter criado o gestor pelo CLI — e esta
    rota não pode virar um cadastro aberto de gestores.
    """
    if not instalacao.sem_gestor(db):
        return RedirectResponse("/gestao/login", status_code=303)
    digitado = {"login": login, "nome": nome}
    try:
        usuario = cfg.criar_gestor(db, login, nome, senha, senha2)
    except cfg.ErroConfiguracao as e:
        return templates.TemplateResponse(request, "gestao/primeiro_acesso.html", {
            "erro": str(e), "v": digitado, "senha_minima": cfg.SENHA_MINIMA,
        }, status_code=400)
    # Auditoria (regra 11): o próprio gestor recém-criado responde pelo ato —
    # não há outro a quem atribuir, e a criação não pode ficar sem registro.
    auditoria.registrar(db, usuario_id=usuario.id, entidade="usuario",
                        entidade_id=usuario.id, acao="criar",
                        depois=auditoria.snapshot(usuario))
    db.commit()
    # Entra direto no assistente: acabou de criar o acesso, o passo seguinte é
    # dizer qual é a OM — não faz sentido devolver a pessoa para o login.
    resp = RedirectResponse("/gestao/instalacao?ok=primeiro-gestor", status_code=303)
    resp.set_cookie(
        "access_token", criar_token(usuario.id),
        httponly=True, samesite="lax", max_age=settings.token_expira_min * 60,
    )
    return resp


# --- Login / logout ---
@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, db: Session = Depends(get_db), erro: str | None = None):
    if instalacao.sem_gestor(db):
        return RedirectResponse("/gestao/primeiro-acesso", status_code=303)
    return templates.TemplateResponse(request, "gestao/login.html", {"erro": erro})


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    # O login vai SEM espaços das pontas: copiar "brigada " de um documento é
    # comum e produziria "login ou senha inválidos" sem explicação. A SENHA não
    # é aparada — recortar caractere de senha é aceitar credencial diferente.
    usuario = db.scalar(select(Usuario).where(Usuario.login == username.strip()))
    hash_ref = usuario.senha_hash if usuario else hash_dummy()
    if not verificar_senha(password, hash_ref) or usuario is None or not usuario.ativo:
        return templates.TemplateResponse(
            request, "gestao/login.html", {"erro": "Login ou senha inválidos."},
            status_code=401,
        )
    resp = RedirectResponse("/gestao", status_code=303)
    resp.set_cookie(
        "access_token", criar_token(usuario.id),
        httponly=True, samesite="lax", max_age=settings.token_expira_min * 60,
    )
    return resp


@router.get("/logout")
def logout():
    resp = RedirectResponse("/gestao/login", status_code=303)
    resp.delete_cookie("access_token")
    return resp


# --- Painel ---
@router.get("", response_class=HTMLResponse)
def painel(request: Request, db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    """Painel do gestor: o que exige ação primeiro, o resto depois.

    Os blocos vêm de `services/painel`; aqui só se monta a tela. A ordem na
    página é deliberada — cobertura e alertas antes de qualquer contagem, porque
    são o que faz alguém deixar de entrar de serviço.
    """
    hoje = date.today()
    amanha = hoje + timedelta(days=1)
    contagens = {
        "militares": db.scalar(select(func.count()).select_from(Militar).where(Militar.ativo.is_(True))),
        "escalas": db.scalar(select(func.count()).select_from(Escala).where(Escala.ativa.is_(True))),
        "servicos": db.scalar(select(func.count()).select_from(Servico)),
    }
    ultimas = db.scalars(
        select(Auditoria).order_by(Auditoria.criado_em.desc(), Auditoria.id.desc()).limit(8)
    ).all()
    # A previsão da fila só é exibida quando amanhã AINDA não está fechado; com o
    # mês fechado (o normal) não se roda o motor à toa.
    amanha_serve = painel_service.servico_do_dia(db, amanha)
    passos_inst = instalacao.passos(db)
    return templates.TemplateResponse(request, "gestao/home.html", {
        "gestor": gestor, "contagens": contagens, "auditoria": ultimas,
        "hoje": hoje, "amanha": amanha,
        "horizonte": painel_service.HORIZONTE_DIAS,
        "cobertura": painel_service.cobertura(db, hoje),
        "alertas": painel_service.alertas(db, hoje),
        "hoje_serve": painel_service.servico_do_dia(db, hoje),
        "amanha_serve": amanha_serve,
        "proximos": [] if amanha_serve else painel_service.proximos_da_fila(db, amanha),
        "saude": painel_service.saude_cadastro(db),
        "equidade": painel_service.equidade(db, date(hoje.year, 1, 1), hoje),
        "feriados_proximos": painel_service.dias_vermelhos_proximos(db, hoje),
        "dias_semana": DIAS_SEMANA,
        # Faixa da instalação, só enquanto houver passo OBRIGATÓRIO pendente:
        # numa instalação em uso ela some, porque aviso permanente vira
        # paisagem. Quem decide é o serviço, não uma contagem repetida aqui.
        "instalacao": None if instalacao.concluida(passos_inst) else {
            "pendentes": instalacao.pendentes(passos_inst),
            "proximo": instalacao.proximo(passos_inst),
            "total": len(passos_inst),
            "feitos": sum(1 for p in passos_inst if p.feito),
        },
    })


@router.get("/instalacao", response_class=HTMLResponse)
def assistente(request: Request, db: Session = Depends(get_db),
               gestor: Usuario = Depends(gestor_web)):
    """Assistente de primeira execução: a ordem certa de instalar numa OM nova.

    As telas para fazer tudo isso já existiam; o que faltava era alguém dizer
    por onde começar. A ordem é a do manual, e cada passo depende do anterior.
    """
    lista = instalacao.passos(db)
    return templates.TemplateResponse(request, "gestao/instalacao.html", {
        "gestor": gestor, "passos": lista,
        "pendentes": instalacao.pendentes(lista),
        "concluida": instalacao.concluida(lista),
        "proximo": instalacao.proximo(lista),
        "feitos": sum(1 for p in lista if p.feito),
    })


# --- Efetivo (CRUD) ---
@router.get("/militares", response_class=HTMLResponse)
def lista_militares(
    request: Request, situacao: str = "", inativos: int = 0, q: str = "",
    posto_graduacao_id: str = "", om_id: str = "",
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    """Efetivo, com busca. Sem ela, achar alguém em 285 linhas é rolar a página.

    A busca casa por nome de guerra OU nome completo, sem diferenciar
    maiúsculas — quem procura digita "souza", não "SOUZA".

    `situacao` tem TRÊS valores (ativos|inativos|todos). Antes havia só o
    `inativos=1`, que não filtrava pelos inativos: apenas removia o filtro dos
    ativos. O link dizia "ver inativos" e trazia o efetivo inteiro — as duas
    telas ficavam iguais. `inativos=1` continua aceito e significa "todos",
    que é o que ele sempre fez, para não quebrar URL guardada.
    """
    if situacao not in ("ativos", "inativos", "todos"):
        situacao = "todos" if inativos else "ativos"
    stmt = (select(Militar)
            .options(joinedload(Militar.posto_graduacao), joinedload(Militar.om))
            .order_by(Militar.nome_guerra))
    if situacao == "ativos":
        stmt = stmt.where(Militar.ativo.is_(True))
    elif situacao == "inativos":
        stmt = stmt.where(Militar.ativo.is_(False))
    termo = q.strip()
    if termo:
        alvo = f"%{termo}%"
        stmt = stmt.where(or_(Militar.nome_guerra.ilike(alvo),
                              Militar.nome_completo.ilike(alvo)))
    if posto_graduacao_id.strip().isdigit():
        stmt = stmt.where(Militar.posto_graduacao_id == int(posto_graduacao_id))
    if om_id.strip().isdigit():
        stmt = stmt.where(Militar.om_id == int(om_id))

    achados = db.scalars(stmt).all()
    # Total sem busca, para a tela dizer "12 de 285" e não mentir sobre o efetivo.
    # É o total DA MESMA situação — comparar 12 inativos com 285 ativos não diz nada.
    conta = select(func.count()).select_from(Militar)
    if situacao == "ativos":
        conta = conta.where(Militar.ativo.is_(True))
    elif situacao == "inativos":
        conta = conta.where(Militar.ativo.is_(False))
    total = db.scalar(conta)
    postos, oms = _refs(db)
    return templates.TemplateResponse(request, "gestao/militares.html", {
        "gestor": gestor, "militares": achados, "situacao": situacao,
        "total": total, "postos": postos, "oms": oms,
        "f": {"q": termo, "posto_graduacao_id": posto_graduacao_id, "om_id": om_id},
        "filtrando": bool(termo or posto_graduacao_id.strip() or om_id.strip()),
    })


def _refs(db: Session, *, so_ativos: bool = False, manter: str | int | None = None):
    """Listas de referência dos formulários e dos filtros.

    `so_ativos` esconde o posto/graduação que a OM desativou em Configurações —
    ninguém deve cadastrar alguém numa graduação fora de uso. `manter` traz de
    volta a graduação do militar que está sendo editado, ainda que desativada:
    sem ela o `<select>` gravaria em silêncio uma patente diferente da dele.
    """
    stmt = select(PostoGraduacao).order_by(PostoGraduacao.ordem_hierarquica.desc())
    if so_ativos:
        atual = int(manter) if str(manter or "").strip().isdigit() else -1
        stmt = stmt.where((PostoGraduacao.ativo.is_(True)) | (PostoGraduacao.id == atual))
    postos = db.scalars(stmt).all()
    oms = db.scalars(select(OrganizacaoMilitar).order_by(OrganizacaoMilitar.sigla)).all()
    return postos, oms


def _form_militar(request, db, gestor, v, erro=None, militar_id=None, status=200, avisos=None):
    postos, oms = _refs(db, so_ativos=True, manter=v.get("posto_graduacao_id"))
    return templates.TemplateResponse(request, "gestao/militar_form.html", {
        "gestor": gestor, "postos": postos, "oms": oms,
        "v": v, "erro": erro, "militar_id": militar_id, "avisos": avisos or [],
    }, status_code=status)


def _ler_form(db, v):
    """Normaliza os campos do formulário e valida (FK/unicidade 9.5 reusa a API).

    Retorna (dados, None) ou (None, mensagem_de_erro). Datas/int vazios -> None.
    """
    def data(s):
        s = (s or "").strip()
        return date.fromisoformat(s) if s else None

    nome_guerra = (v["nome_guerra"] or "").strip()
    nome_completo = (v["nome_completo"] or "").strip()
    if not nome_guerra or not nome_completo:
        return None, "Nome de guerra e nome completo são obrigatórios."
    try:
        posto_id, om_id = int(v["posto_graduacao_id"]), int(v["om_id"])
    except (ValueError, TypeError):
        return None, "Selecione posto/graduação e OM."
    # Só dígitos, como a ficha grava: com a máscara, '605.126.360-87' e
    # '60512636087' escapariam do UNIQUE e criariam dois cadastros da mesma
    # pessoa concorrendo na fila. Digitado sem nenhum dígito é erro, não None.
    identidade, cpf = so_digitos(v["identidade"]), so_digitos(v["cpf"])
    for rotulo, bruto, limpo in (("Identidade", v["identidade"], identidade),
                                 ("CPF", v["cpf"], cpf)):
        if (bruto or "").strip() and limpo is None:
            return None, f"{rotulo} deve conter dígitos."
    try:
        dados = {
            "nome_guerra": nome_guerra,
            "nome_completo": nome_completo,
            "posto_graduacao_id": posto_id,
            "om_id": om_id,
            "identidade": identidade,
            "cpf": cpf,
            "data_promocao": data(v["data_promocao"]),
            "data_praca": data(v["data_praca"]),
            "data_nascimento": data(v["data_nascimento"]),
            "numero_antiguidade": int(v["numero_antiguidade"]) if (v["numero_antiguidade"] or "").strip() else None,
        }
    except ValueError:
        return None, "Data ou número inválido."
    try:
        _validar_fks_e_antiguidade(
            db, posto_graduacao_id=dados["posto_graduacao_id"],
            om_id=dados["om_id"], numero_antiguidade=dados["numero_antiguidade"])
    except HTTPException as e:
        return None, str(e.detail)
    return dados, None


_CAMPOS = ("nome_guerra", "nome_completo", "posto_graduacao_id", "om_id", "identidade",
           "cpf", "data_promocao", "data_praca", "data_nascimento", "numero_antiguidade")


@router.get("/militares/novo", response_class=HTMLResponse)
def militar_novo(request: Request, db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    return _form_militar(request, db, gestor, v={})


@router.post("/militares", response_class=HTMLResponse)
def militar_criar(
    request: Request,
    nome_guerra: str = Form(""), nome_completo: str = Form(""),
    posto_graduacao_id: str = Form(""), om_id: str = Form(""),
    identidade: str = Form(""), cpf: str = Form(""),
    data_promocao: str = Form(""), data_praca: str = Form(""),
    data_nascimento: str = Form(""), numero_antiguidade: str = Form(""),
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    v = {c: locals()[c] for c in _CAMPOS}
    dados, erro = _ler_form(db, v)
    if erro:
        return _form_militar(request, db, gestor, v, erro, status=400)
    m = Militar(**dados)
    db.add(m)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return _form_militar(request, db, gestor, v, "Identidade ou CPF já cadastrado.", status=409)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="militar", entidade_id=m.id,
                        acao="criar", depois=auditoria.snapshot(m))
    db.commit()
    return RedirectResponse("/gestao/militares?ok=militar-criado", status_code=303)


# --- Importação da ficha individual (PDF) — pré-preenche o formulário ---
# Declarada ANTES de POST /militares/{militar_id}: o roteamento é por ordem e
# '/militares/importar' cairia na rota de id.
def _importar_para_form(request, db, gestor, arquivo, base: dict, militar_id=None):
    """Lê a ficha e devolve o FORMULÁRIO pré-preenchido — nada é gravado.

    `base` são os valores que já estavam na tela (o que o operador digitou, e na
    edição o cadastro atual por baixo); a ficha só sobrescreve o que ela própria
    traz, para não apagar dado existente com campo que a ficha não tem.
    """
    # O campo de arquivo mora no formulário principal (não pode ser `required`,
    # senão travaria o Salvar), então clicar em 'Ler ficha' sem escolher o PDF é
    # caso normal — vira mensagem, não 422.
    if arquivo is None or not (arquivo.filename or "").strip():
        return _form_militar(request, db, gestor, base,
                             "Selecione o arquivo PDF da ficha.",
                             militar_id=militar_id, status=400)
    try:
        lida = ler_ficha(arquivo.file.read(MAX_FICHA_BYTES + 1), db, militar_id)
    except HTTPException as e:
        return _form_militar(request, db, gestor, base, str(e.detail),
                             militar_id=militar_id, status=400)
    v = dict(base)
    v.update({campo: valor
              for campo, valor in importacao.para_formulario(lida.militar.model_dump()).items()
              if valor != ""})
    avisos = [f"Ficha lida no formato {lida.formato.upper()}. "
              "Confira os campos antes de salvar — nada foi gravado ainda.", *lida.avisos]
    return _form_militar(request, db, gestor, v, militar_id=militar_id, avisos=avisos)


def _preenchidos(v: dict) -> dict:
    """Só os campos que o operador realmente digitou (descarta os vazios)."""
    return {campo: valor for campo, valor in v.items() if str(valor).strip()}


@router.post("/militares/importar", response_class=HTMLResponse)
def militar_importar_novo(
    request: Request, arquivo: UploadFile | None = File(None),
    nome_guerra: str = Form(""), nome_completo: str = Form(""),
    posto_graduacao_id: str = Form(""), om_id: str = Form(""),
    identidade: str = Form(""), cpf: str = Form(""),
    data_promocao: str = Form(""), data_praca: str = Form(""),
    data_nascimento: str = Form(""), numero_antiguidade: str = Form(""),
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    """Lê a ficha no cadastro NOVO, sobre o que já estava digitado.

    O botão de importar submete o formulário inteiro (`formaction`), então o que
    o operador já preencheu volta na tela — inclusive o nº de antiguidade, que a
    ficha nunca traz (regra 9.5).
    """
    v = {c: locals()[c] for c in _CAMPOS}
    return _importar_para_form(request, db, gestor, arquivo, base=_preenchidos(v))


@router.post("/militares/{militar_id}/importar", response_class=HTMLResponse)
def militar_importar_edicao(
    militar_id: int, request: Request, arquivo: UploadFile | None = File(None),
    nome_guerra: str = Form(""), nome_completo: str = Form(""),
    posto_graduacao_id: str = Form(""), om_id: str = Form(""),
    identidade: str = Form(""), cpf: str = Form(""),
    data_promocao: str = Form(""), data_praca: str = Form(""),
    data_nascimento: str = Form(""), numero_antiguidade: str = Form(""),
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    """Completa pela ficha um militar já cadastrado — é o caso da carga da
    planilha, que entrou só com nome + posto + OM.

    A base é o cadastro atual, coberto pelo que veio do formulário: assim uma
    alteração ainda não salva não se perde ao ler a ficha.
    """
    m = db.get(Militar, militar_id)
    if m is None:
        return RedirectResponse("/gestao/militares", status_code=303)
    v = {c: locals()[c] for c in _CAMPOS}
    base = _v_de(m) | _preenchidos(v)
    return _importar_para_form(request, db, gestor, arquivo, base=base, militar_id=m.id)


def _v_de(m: Militar) -> dict:
    def s(d):
        return d.isoformat() if d else ""
    return {"nome_guerra": m.nome_guerra, "nome_completo": m.nome_completo,
            "posto_graduacao_id": m.posto_graduacao_id, "om_id": m.om_id,
            "identidade": m.identidade or "", "cpf": m.cpf or "",
            "data_promocao": s(m.data_promocao), "data_praca": s(m.data_praca),
            "data_nascimento": s(m.data_nascimento),
            "numero_antiguidade": m.numero_antiguidade or ""}


@router.get("/militares/{militar_id}", response_class=HTMLResponse)
def militar_editar(militar_id: int, request: Request, db: Session = Depends(get_db),
                   gestor: Usuario = Depends(gestor_web)):
    m = db.get(Militar, militar_id)
    if m is None:
        return RedirectResponse("/gestao/militares", status_code=303)
    return _form_militar(request, db, gestor, _v_de(m), militar_id=m.id)


@router.post("/militares/{militar_id}", response_class=HTMLResponse)
def militar_atualizar(
    militar_id: int, request: Request,
    nome_guerra: str = Form(""), nome_completo: str = Form(""),
    posto_graduacao_id: str = Form(""), om_id: str = Form(""),
    identidade: str = Form(""), cpf: str = Form(""),
    data_promocao: str = Form(""), data_praca: str = Form(""),
    data_nascimento: str = Form(""), numero_antiguidade: str = Form(""),
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    m = db.get(Militar, militar_id)
    if m is None:
        return RedirectResponse("/gestao/militares", status_code=303)
    v = {c: locals()[c] for c in _CAMPOS}
    dados, erro = _ler_form(db, v)
    if erro:
        return _form_militar(request, db, gestor, v, erro, militar_id=militar_id, status=400)
    antes = auditoria.snapshot(m)
    for campo, valor in dados.items():
        setattr(m, campo, valor)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return _form_militar(request, db, gestor, v, "Identidade ou CPF já cadastrado.",
                             militar_id=militar_id, status=409)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="militar", entidade_id=m.id,
                        acao="alterar", antes=antes, depois=auditoria.snapshot(m))
    db.commit()
    return RedirectResponse("/gestao/militares?ok=militar-alterado", status_code=303)


@router.post("/militares/{militar_id}/desativar")
def militar_desativar(militar_id: int, db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    m = db.get(Militar, militar_id)
    if m is not None and m.ativo:
        antes = auditoria.snapshot(m)
        m.ativo = False
        auditoria.registrar(db, usuario_id=gestor.id, entidade="militar", entidade_id=m.id,
                            acao="excluir", antes=antes, depois=auditoria.snapshot(m))
        db.commit()
    return RedirectResponse("/gestao/militares?ok=militar-desativado", status_code=303)


@router.post("/militares/{militar_id}/reativar")
def militar_reativar(militar_id: int, db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    m = db.get(Militar, militar_id)
    if m is not None and not m.ativo:
        antes = auditoria.snapshot(m)
        m.ativo = True
        auditoria.registrar(db, usuario_id=gestor.id, entidade="militar", entidade_id=m.id,
                            acao="alterar", antes=antes, depois=auditoria.snapshot(m))
        db.commit()
    return RedirectResponse("/gestao/militares?situacao=inativos&ok=militar-reativado",
                            status_code=303)


# --- Escalação de período ---
@router.get("/escalar", response_class=HTMLResponse)
def escalar_form(request: Request, db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    escalas = db.scalars(select(Escala).where(Escala.ativa.is_(True)).order_by(Escala.nome)).all()
    return templates.TemplateResponse(request, "gestao/escalar.html", {
        "gestor": gestor, "escalas": escalas, "resultado": None, "erro": None,
    })


def _permutas_do_periodo(db: Session, escala_id: int, d_ini: date, d_fim: date) -> list[dict]:
    """Retrato das permutas do período — é o que o 'regravar' apaga por CASCADE.

    Fotografa ANTES do delete: depois não há como saber o que se perdeu (a
    permuta some junto com o serviço e não deixa rastro na tabela).
    """
    linhas = db.execute(
        select(Permuta, Servico)
        .join(Servico, Permuta.servico_id == Servico.id)
        .where(Servico.escala_id == escala_id, Servico.dia >= d_ini, Servico.dia <= d_fim)
        .order_by(Servico.dia)
    ).all()
    if not linhas:
        return []
    ids = {s.militar_id for _, s in linhas} | {p.militar_substituto_id for p, _ in linhas}
    nome = dict(db.execute(
        select(Militar.id, Militar.nome_guerra).where(Militar.id.in_(ids))).all())
    return [
        {"dia": s.dia.isoformat(),
         "escalado_id": s.militar_id, "escalado": nome.get(s.militar_id),
         "substituto_id": p.militar_substituto_id,
         "substituto": nome.get(p.militar_substituto_id),
         "observacao": p.observacao}
        for p, s in linhas
    ]


@router.post("/escalar", response_class=HTMLResponse)
def escalar(
    request: Request,
    escala_id: int = Form(...),
    inicio: str = Form(...),
    fim: str = Form(...),
    regravar: str | None = Form(None),
    db: Session = Depends(get_db),
    gestor: Usuario = Depends(gestor_web),
):
    escalas = db.scalars(select(Escala).where(Escala.ativa.is_(True)).order_by(Escala.nome)).all()

    def erro(msg: str):
        return templates.TemplateResponse(request, "gestao/escalar.html", {
            "gestor": gestor, "escalas": escalas, "resultado": None, "erro": msg,
        }, status_code=400)

    try:
        d_ini, d_fim = date.fromisoformat(inicio), date.fromisoformat(fim)
    except ValueError:
        return erro("Datas inválidas.")
    if d_fim < d_ini:
        return erro("'fim' anterior a 'início'.")
    if (d_fim - d_ini).days + 1 > MAX_DIAS:
        return erro(f"Período máximo de {MAX_DIAS} dias.")
    escala = db.get(Escala, escala_id)
    if escala is None:
        return erro("Escala não encontrada.")

    # regravar: apaga os serviços do período antes (senão a escalação é idempotente
    # e um mês já fechado não muda ao re-escalar). Destrutivo — só quando pedido.
    # As permutas do período vão junto (ON DELETE CASCADE), e permuta é registro
    # manual do gestor: some da tela, é anunciada e fica na auditoria (regra 11).
    permutas_perdidas: list[dict] = []
    if regravar:
        permutas_perdidas = _permutas_do_periodo(db, escala_id, d_ini, d_fim)
        db.execute(delete(Servico).where(
            Servico.escala_id == escala_id, Servico.dia >= d_ini, Servico.dia <= d_fim))

    resultados = rotacao.escalar_e_gravar_periodo(db, escala_id, d_ini, d_fim)
    dias_com_alerta = sum(1 for r in resultados if r.efetivo_insuficiente)
    # o que foi GRAVADO; sem 'regravar', um período já fechado grava zero
    servicos = sum(r.servicos_gravados or 0 for r in resultados)
    pretendidos = sum(len(r.escolhidos) for r in resultados)
    auditoria.registrar(
        db, usuario_id=gestor.id, entidade="escala", entidade_id=escala_id, acao="escalar",
        depois={"inicio": inicio, "fim": fim, "dias": len(resultados),
                "servicos_intencionados": pretendidos, "servicos_gravados": servicos,
                "dias_com_alerta": dias_com_alerta, "regravar": bool(regravar),
                "permutas_apagadas": permutas_perdidas},
    )
    db.commit()

    resultado = {
        "escala": escala, "inicio": d_ini, "fim": d_fim,
        "dias": len(resultados), "servicos": servicos, "pretendidos": pretendidos,
        "alertas": dias_com_alerta, "permutas_apagadas": permutas_perdidas,
        "ano": d_ini.year, "mes": d_ini.month,
    }
    return templates.TemplateResponse(request, "gestao/escalar.html", {
        "gestor": gestor, "escalas": escalas, "resultado": resultado, "erro": None,
    })


# --- Impedimentos (dispensa/férias/curso — regra 7.5) ---
def _militares_ativos(db: Session):
    return db.scalars(
        select(Militar).where(Militar.ativo.is_(True))
        .options(joinedload(Militar.posto_graduacao), joinedload(Militar.om))
        .order_by(Militar.nome_guerra)
    ).all()


def agrupar_por_posto(militares) -> list[tuple[str, list]]:
    """Militares em grupos de posto/graduação, do mais antigo para o mais moderno.

    Uma lista corrida de 285 nomes é impossível de varrer num `<select>`; por
    posto/graduação o gestor acha em dois saltos, porque é assim que ele pensa
    o efetivo. Sem a ordem hierárquica os grupos sairiam em ordem alfabética,
    que não quer dizer nada aqui.
    """
    grupos: dict[str, list] = {}
    for m in militares:
        grupos.setdefault(m.posto_graduacao.sigla, []).append(m)
    ordem = {m.posto_graduacao.sigla: m.posto_graduacao.ordem_hierarquica
             for m in militares}
    return sorted(grupos.items(), key=lambda kv: -ordem[kv[0]])


def _tela_impedimentos(request, db, gestor, erro=None, status=200, militar_id=None):
    """Tela de impedimentos, opcionalmente no contexto de UM militar.

    Com `militar_id` (é como o Efetivo chega aqui) a tela vira a ficha de
    impedimentos daquela pessoa: formulário já apontando para ela e lista só com
    os dela — numa OM de 285 militares, cair na lista geral obrigaria a procurar.
    """
    foco = db.scalar(
        select(Militar).options(joinedload(Militar.posto_graduacao), joinedload(Militar.om))
        .where(Militar.id == militar_id)
    ) if militar_id is not None else None

    stmt = (select(Impedimento).options(joinedload(Impedimento.tipo))
            .order_by(Impedimento.inicio.desc()))
    if foco is not None:
        stmt = stmt.where(Impedimento.militar_id == foco.id)
    impedimentos = db.scalars(stmt.limit(200)).all()

    mil = {m.id: m for m in db.scalars(
        select(Militar).options(joinedload(Militar.posto_graduacao)))}
    return templates.TemplateResponse(request, "gestao/impedimentos.html", {
        "gestor": gestor, "erro": erro,
        "militares": agrupar_por_posto(_militares_ativos(db)),
        # só os tipos em uso: o desativado em Configurações some do formulário,
        # mas os impedimentos já lançados com ele continuam valendo
        "tipos": db.scalars(select(TipoImpedimento)
                            .where(TipoImpedimento.ativo.is_(True))
                            .order_by(TipoImpedimento.nome)).all(),
        "impedimentos": impedimentos, "mil": mil, "foco": foco, "hoje": date.today(),
        "linha": painel_service.linha_do_tempo(impedimentos, mil, date.today()),
    }, status_code=status)


@router.get("/impedimentos", response_class=HTMLResponse)
def impedimentos_form(
    request: Request, militar_id: int | None = None,
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    """`?militar_id=` abre no contexto de um militar (link vindo do Efetivo)."""
    return _tela_impedimentos(request, db, gestor, militar_id=militar_id)


def _volta_para(contexto: str, ok: str) -> str:
    """Para onde voltar depois de gravar: a ficha do militar, se viemos dela.

    `ok` é a chave da confirmação (ver AVISOS em app/web/__init__.py).
    """
    contexto = (contexto or "").strip()
    if contexto.isdigit():
        return f"/gestao/impedimentos?militar_id={contexto}&ok={ok}"
    return f"/gestao/impedimentos?ok={ok}"


@router.post("/impedimentos", response_class=HTMLResponse)
def criar_impedimento(
    request: Request,
    militar_id: int = Form(...),
    tipo_impedimento_id: int = Form(...),
    inicio: str = Form(...),
    fim: str = Form(...),
    observacao: str = Form(""),
    contexto: str = Form(""),      # militar em foco na tela (vazio = lista geral)
    db: Session = Depends(get_db),
    gestor: Usuario = Depends(gestor_web),
):
    foco = int(contexto) if contexto.strip().isdigit() else None

    def erro(msg: str):
        return _tela_impedimentos(request, db, gestor, msg, 400, militar_id=foco)

    if db.get(Militar, militar_id) is None:
        return erro("Militar inexistente.")
    if db.get(TipoImpedimento, tipo_impedimento_id) is None:
        return erro("Tipo inexistente.")
    try:
        d_ini, d_fim = date.fromisoformat(inicio), date.fromisoformat(fim)
    except ValueError:
        return erro("Datas inválidas.")
    if d_fim < d_ini:
        return erro("'Fim' anterior ao 'início'.")

    imp = Impedimento(militar_id=militar_id, tipo_impedimento_id=tipo_impedimento_id,
                      inicio=d_ini, fim=d_fim, observacao=(observacao.strip() or None))
    db.add(imp)
    db.flush()
    auditoria.registrar(db, usuario_id=gestor.id, entidade="impedimento",
                        entidade_id=imp.id, acao="criar", depois=auditoria.snapshot(imp))
    db.commit()
    return RedirectResponse(_volta_para(contexto, "impedimento-criado"), status_code=303)


@router.post("/impedimentos/{imp_id}/remover")
def remover_impedimento(
    imp_id: int, contexto: str = Form(""),
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    imp = db.get(Impedimento, imp_id)
    if imp is not None:
        antes = auditoria.snapshot(imp)
        db.delete(imp)
        auditoria.registrar(db, usuario_id=gestor.id, entidade="impedimento",
                            entidade_id=imp_id, acao="excluir", antes=antes)
        db.commit()
    return RedirectResponse(_volta_para(contexto, "impedimento-removido"), status_code=303)


# --- Permutas (regra 9) ---
# Duas telas: o MÊS (lista quem serve e o que já foi permutado) e o SERVIÇO
# escolhido (form da troca). Separado de propósito: um select de substitutos em
# cada uma das 31 linhas geraria milhares de <option> por página.
def _mes_da_escala(db: Session, escala_id: int) -> tuple[int, int]:
    ultimo = db.scalar(select(func.max(Servico.dia)).where(Servico.escala_id == escala_id))
    d = ultimo or date.today()
    return d.year, d.month


def _tela_permutas(request, db, gestor, escala, ano, mes, erro=None, status=200):
    primeiro = date(ano, mes, 1)
    ultimo = date(ano, mes, monthrange(ano, mes)[1])
    servicos = db.scalars(
        select(Servico)
        .where(Servico.escala_id == escala.id, Servico.dia >= primeiro, Servico.dia <= ultimo)
        .options(joinedload(Servico.militar).joinedload(Militar.posto_graduacao))
        .order_by(Servico.dia, Servico.posto_id)
    ).all()

    permutas = {p.servico_id: p for p in db.scalars(
        select(Permuta).where(Permuta.servico_id.in_([s.id for s in servicos])))
    } if servicos else {}
    subs = {m.id: m for m in db.scalars(
        select(Militar).options(joinedload(Militar.posto_graduacao))
        .where(Militar.id.in_({p.militar_substituto_id for p in permutas.values()})))
    } if permutas else {}

    prev = (ano - 1, 12) if mes == 1 else (ano, mes - 1)
    prox = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return templates.TemplateResponse(request, "gestao/permutas.html", {
        "gestor": gestor, "erro": erro,
        "escalas": db.scalars(select(Escala).where(Escala.ativa.is_(True)).order_by(Escala.nome)).all(),
        "escala": escala, "ano": ano, "mes": mes, "mes_nome": MESES[mes],
        "servicos": servicos, "permutas": permutas, "subs": subs,
        "dias_semana": DIAS_SEMANA, "prev": prev, "prox": prox,
    }, status_code=status)


@router.get("/permutas", response_class=HTMLResponse)
def permutas_mes(
    request: Request,
    escala_id: int | None = None,
    ano: int | None = Query(None, ge=ANO_MIN, le=ANO_MAX),
    mes: int | None = Query(None, ge=1, le=12),
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    escalas = db.scalars(select(Escala).where(Escala.ativa.is_(True)).order_by(Escala.nome)).all()
    if not escalas:
        return templates.TemplateResponse(request, "gestao/permutas.html", {
            "gestor": gestor, "escalas": [], "escala": None, "servicos": [],
        })
    escala = next((e for e in escalas if e.id == escala_id), escalas[0])
    if ano is None or mes is None:
        ano, mes = _mes_da_escala(db, escala.id)
    return _tela_permutas(request, db, gestor, escala, ano, mes)


def _tela_permuta_servico(request, db, gestor, servico, erro=None, status=200):
    """Form da troca. Oferece os participantes ATIVOS da escala (menos o
    escalado) — é quem cobre na prática, e a lista fica utilizável."""
    escala = db.get(Escala, servico.escala_id)
    candidatos = db.scalars(
        select(Militar)
        .join(Participacao, Participacao.militar_id == Militar.id)
        .where(Participacao.escala_id == servico.escala_id,
               Participacao.ativo.is_(True),
               Militar.ativo.is_(True),
               Militar.id != servico.militar_id)
        .options(joinedload(Militar.posto_graduacao), joinedload(Militar.om))
        .order_by(Militar.nome_guerra)
    ).all()
    escalado = db.scalar(
        select(Militar).options(joinedload(Militar.posto_graduacao), joinedload(Militar.om))
        .where(Militar.id == servico.militar_id))
    return templates.TemplateResponse(request, "gestao/permuta_form.html", {
        "gestor": gestor, "erro": erro, "servico": servico, "escala": escala,
        "escalado": escalado, "candidatos": agrupar_por_posto(candidatos),
    }, status_code=status)


@router.get("/permutas/servico/{servico_id}", response_class=HTMLResponse)
def permuta_form(
    servico_id: int, request: Request,
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    servico = db.get(Servico, servico_id)
    if servico is None:
        return RedirectResponse("/gestao/permutas", status_code=303)
    return _tela_permuta_servico(request, db, gestor, servico)


@router.post("/permutas/servico/{servico_id}", response_class=HTMLResponse)
def registrar_permuta_web(
    servico_id: int, request: Request,
    militar_substituto_id: str = Form(""), observacao: str = Form(""),
    db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    servico = db.get(Servico, servico_id)
    if servico is None:
        return RedirectResponse("/gestao/permutas", status_code=303)
    try:
        substituto_id = int(militar_substituto_id)
    except (TypeError, ValueError):
        return _tela_permuta_servico(request, db, gestor, servico, "Selecione o substituto.", 400)

    try:
        p = permuta_service.registrar_permuta(
            db, servico_id=servico_id, militar_substituto_id=substituto_id,
            autorizado_por=gestor.id, observacao=(observacao.strip() or None))
    except ValueError as e:
        return _tela_permuta_servico(request, db, gestor, servico, str(e), 400)
    except permuta_service.PermutaNegada as e:
        # Regra 10.5 e guardas: a recusa é informação, não erro de sistema.
        return _tela_permuta_servico(request, db, gestor, servico,
                                     f"Permuta negada: {e.motivo}.", 400)

    auditoria.registrar(db, usuario_id=gestor.id, entidade="permuta", entidade_id=p.id,
                        acao="criar", depois=auditoria.snapshot(p))
    db.commit()
    return RedirectResponse(
        f"/gestao/permutas?escala_id={servico.escala_id}"
        f"&ano={servico.dia.year}&mes={servico.dia.month}&ok=permuta-criada",
        status_code=303)


@router.post("/permutas/servico/{servico_id}/cancelar")
def cancelar_permuta_web(
    servico_id: int, db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web),
):
    servico = db.get(Servico, servico_id)
    if servico is None:
        return RedirectResponse("/gestao/permutas", status_code=303)
    p = db.scalar(select(Permuta).where(Permuta.servico_id == servico_id))
    if p is not None:
        antes, permuta_id = auditoria.snapshot(p), p.id
        permuta_service.cancelar_permuta(db, servico_id)
        auditoria.registrar(db, usuario_id=gestor.id, entidade="permuta",
                            entidade_id=permuta_id, acao="excluir", antes=antes)
        db.commit()
    return RedirectResponse(
        f"/gestao/permutas?escala_id={servico.escala_id}"
        f"&ano={servico.dia.year}&mes={servico.dia.month}&ok=permuta-cancelada",
        status_code=303)
