"""Tela de BACKUP, RESTAURAÇÃO e EXPORTAÇÃO (protegida — regra 11).

O sistema roda na própria OM (regra 13.3): não existe operação central que
guarde nada. Se o servidor se perder, quem tem a cópia é quem clicou aqui — e
por isso as três coisas cabem numa tela só, sem terminal.

São três assuntos com pesos diferentes, e a tela os apresenta nessa ordem:

  1. **Baixar backup** — o arquivo que restaura tudo. Um clique.
  2. **Exportar dados** — CSV para ler fora do sistema. NÃO restaura nada.
  3. **Restaurar** — a ação destrutiva, por último e em duas etapas.

Por que baixar é POST e não link: a tela precisa saber **quando foi o último
backup** para poder cobrar (o cartão do hub mostra isso), e um GET que grava
seria mentira sobre o método. De quebra, o gestor continua na mesma página
depois de baixar.

Na restauração, o gestor **é avisado e decide** quando o backup não contém o
próprio login (decisão do usuário, 2026-07-28): recusar impediria o caso
legítimo de restaurar um backup antigo depois de recriar contas. O que a tela
não pode é deixar isso passar em silêncio — quem restaurar assim perde o acesso
e só o CLI recria (`python -m app.seeds.usuario`).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app import VERSAO
from app.database import get_db
from app.models.gestao import Usuario
from app.services import auditoria
from app.services import backup as bkp
from app.services import configuracao as cfg
from app.services import exportacao
from app.web import templates
from app.web.gestao import gestor_web

router = APIRouter(prefix="/gestao/configuracao/backup", tags=["web-gestão"])


def _cartao(db: Session):
    return next((c for c in cfg.panorama(db) if c.chave == "backup"), None)


def _tela(request: Request, db: Session, gestor: Usuario, retrato=None,
          token: str = "", erro: str | None = None, status: int = 200):
    return templates.TemplateResponse(request, "gestao/config/backup.html", {
        "gestor": gestor,
        "erro": erro,
        "cartao": _cartao(db),
        "secao": "backup",
        "em_arquivo": bkp.eh_arquivo(),
        "caminho": bkp.caminho_do_banco(),
        "ultimo_backup": cfg.valor(db, "ultimo_backup_em"),
        "automaticos": bkp.automaticos(),
        "manter": bkp.MANTER_AUTOMATICOS,
        "retrato": retrato,
        "token": token,
        "eu": gestor.login,
    }, status_code=status)


@router.get("", response_class=HTMLResponse)
def backup(request: Request, db: Session = Depends(get_db),
           gestor: Usuario = Depends(gestor_web)):
    return _tela(request, db, gestor)


# --- 1. baixar ----------------------------------------------------------------
@router.post("/baixar")
def baixar(request: Request, db: Session = Depends(get_db),
           gestor: Usuario = Depends(gestor_web)):
    """O banco inteiro, num arquivo. É isto que restaura a instalação."""
    agora = datetime.now()
    # Antes da cópia, e com commit: os dois carimbos precisam estar DENTRO do
    # arquivo que vai ser baixado — é lá que alguém vai procurá-los, um dia,
    # para saber se aquele backup serve.
    cfg.definir(db, "versao_aplicacao", VERSAO)
    cfg.definir(db, "ultimo_backup_em", agora.isoformat(timespec="minutes"))
    db.commit()

    try:
        conteudo = bkp.copia(db)
    except bkp.ErroBackup as e:
        return _tela(request, db, gestor, erro=str(e), status=400)

    nome = bkp.nome_sugerido(cfg.identificacao(db).sigla, agora)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="backup", acao="criar",
                        depois={"arquivo": nome, "bytes": len(conteudo)})
    db.commit()
    return Response(
        content=conteudo,
        media_type="application/vnd.sqlite3",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@router.get("/automatico/{nome}")
def baixar_automatico(nome: str, request: Request, db: Session = Depends(get_db),
                      gestor: Usuario = Depends(gestor_web)):
    """Baixa um dos backups automáticos.

    É o que tira "o estado de ontem" da máquina que está com problema: os
    automáticos moram no mesmo disco, e enquanto o sistema ainda sobe, este link
    é o caminho mais curto para pôr o arquivo em outro lugar.
    """
    try:
        arquivo = bkp.arquivo_automatico(nome)
    except bkp.ErroBackup as e:
        return _tela(request, db, gestor, erro=str(e), status=404)
    return Response(
        content=arquivo.read_bytes(),
        media_type="application/vnd.sqlite3",
        headers={"Content-Disposition": f'attachment; filename="{arquivo.name}"'},
    )


@router.post("/automatico")
def gerar_agora(request: Request, db: Session = Depends(get_db),
                gestor: Usuario = Depends(gestor_web)):
    """Força o backup automático do dia (o laço de fundo só olha de hora em hora).

    Existe para o momento exato do problema: quem vai desligar a máquina agora
    não pode depender da próxima passagem do laço.
    """
    cfg.definir(db, "versao_aplicacao", VERSAO)
    db.commit()
    bkp.gerar_automatico(forcar=True)
    return RedirectResponse("/gestao/configuracao/backup?ok=backup-automatico-gerado",
                            status_code=303)


# --- 2. exportar --------------------------------------------------------------
@router.post("/exportar")
def exportar(request: Request, dados_pessoais: str = Form(""),
             db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    """ZIP de CSVs para ler fora do sistema. Não restaura nada — e o LEIA-ME
    dentro do pacote diz isso na primeira linha."""
    incluir = dados_pessoais == "1"
    conteudo = exportacao.pacote(db, incluir_pessoais=incluir)
    nome = exportacao.nome_do_pacote(cfg.identificacao(db).sigla)
    auditoria.registrar(db, usuario_id=gestor.id, entidade="exportacao", acao="criar",
                        depois={"arquivo": nome, "dados_pessoais": incluir})
    db.commit()
    return Response(
        content=conteudo,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


# --- 3. restaurar (duas etapas) -----------------------------------------------
@router.post("/restaurar")
def conferir(request: Request, arquivo: UploadFile | None = File(None),
             db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    """Etapa 1: recebe o arquivo e mostra o que ele contém. Nada é trocado."""
    if arquivo is None or not arquivo.filename:
        return _tela(request, db, gestor,
                     erro="Escolha o arquivo de backup (.sqlite3) antes de conferir.",
                     status=400)
    try:
        token = bkp.guardar_envio(arquivo.file.read())
        retrato = bkp.inspecionar(bkp.caminho_envio(token))
    except bkp.ErroBackup as e:
        return _tela(request, db, gestor, erro=str(e), status=400)
    return _tela(request, db, gestor, retrato=retrato, token=token)


@router.post("/restaurar/confirmar")
def confirmar(request: Request, token: str = Form(""),
              db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    """Etapa 2: põe o arquivo conferido no lugar do banco atual.

    A sessão do pedido é fechada ANTES da troca: ela está ligada ao arquivo que
    vai sair de cena. O registro da restauração é gravado numa sessão nova,
    contra o banco RESTAURADO — é lá que ele precisa aparecer, e é a primeira
    linha do histórico da instalação recuperada.
    """
    login = gestor.login
    try:
        bkp.caminho_envio(token)              # peneira o token antes de fechar nada
    except bkp.ErroBackup as e:
        return _tela(request, db, gestor, erro=str(e), status=400)

    db.close()
    try:
        feito = bkp.restaurar(token)
    except bkp.ErroBackup as e:
        return _tela(request, db, gestor, erro=str(e), status=400)

    _registrar_restauracao(login, feito)
    return RedirectResponse("/gestao/configuracao/backup?ok=backup-restaurado",
                            status_code=303)


# --- restaurar numa MÁQUINA NOVA (antes de existir gestor) --------------------
# É o cenário que decide a compra do sistema: "esta máquina está com problema,
# preciso subir outra com o estado de ontem". Sem esta porta, a única saída seria
# criar um gestor descartável pelo primeiro acesso, entrar, restaurar — e ver a
# restauração apagar esse gestor. Funciona por acidente, e é o caminho errado
# para ensinar a quem está com o servidor caindo.
#
# A guarda é a MESMA do primeiro acesso (`instalacao.sem_gestor`), rechecada no
# POST: fecha-se sozinha assim que existe gestor. A exposição é a que já existe
# — quem alcança um sistema sem gestor já podia criar o primeiro por lá; aqui,
# além disso, precisa ter em mãos um backup válido desta OM.
router_instalacao = APIRouter(prefix="/gestao/restaurar-instalacao", tags=["web-gestão"])


def _tela_instalacao(request: Request, retrato=None, token: str = "",
                     erro: str | None = None, status: int = 200,
                     senha_instalacao: str = ""):
    from app.config import settings

    return templates.TemplateResponse(request, "gestao/restaurar_instalacao.html", {
        "erro": erro, "retrato": retrato, "token": token,
        "em_arquivo": bkp.eh_arquivo(),
        "arquivo_senha": settings.primeiro_acesso_file,
        # Levada adiante na etapa 2: a senha é conferida NAS DUAS, e obrigar a
        # digitá-la de novo depois da conferência seria atrito sem ganho — quem
        # a acertou na primeira já provou ter acesso ao servidor.
        "senha_instalacao": senha_instalacao,
    }, status_code=status)


def _so_sem_gestor(db: Session):
    from app.services import instalacao

    return None if instalacao.sem_gestor(db) else RedirectResponse(
        "/gestao/login", status_code=303)


_ERRO_SENHA = ("Senha de instalação incorreta. Ela está no arquivo indicado "
               "abaixo, no servidor, e também aparece no log de quando o "
               "sistema subiu.")


@router_instalacao.get("", response_class=HTMLResponse)
def restaurar_instalacao_form(request: Request, db: Session = Depends(get_db)):
    from app.services import instalacao

    fechada = _so_sem_gestor(db)
    if fechada is not None:
        return fechada
    instalacao.senha_primeiro_acesso()        # garante o arquivo, como no /primeiro-acesso
    return _tela_instalacao(request)


@router_instalacao.post("", response_class=HTMLResponse)
def restaurar_instalacao_conferir(request: Request,
                                  arquivo: UploadFile | None = File(None),
                                  senha_instalacao: str = Form(""),
                                  db: Session = Depends(get_db)):
    from app.services import instalacao

    fechada = _so_sem_gestor(db)
    if fechada is not None:
        return fechada
    # Antes de gravar o arquivo enviado: sem a senha, esta rota seria um upload
    # aberto de banco de dados para dentro do servidor.
    if not instalacao.conferir_senha(senha_instalacao):
        return _tela_instalacao(request, erro=_ERRO_SENHA, status=400)
    if arquivo is None or not arquivo.filename:
        return _tela_instalacao(
            request, erro="Escolha o arquivo de backup (.sqlite3).", status=400,
            senha_instalacao=senha_instalacao)
    try:
        token = bkp.guardar_envio(arquivo.file.read())
        retrato = bkp.inspecionar(bkp.caminho_envio(token))
    except bkp.ErroBackup as e:
        return _tela_instalacao(request, erro=str(e), status=400,
                                senha_instalacao=senha_instalacao)
    return _tela_instalacao(request, retrato=retrato, token=token,
                            senha_instalacao=senha_instalacao)


@router_instalacao.post("/confirmar")
def restaurar_instalacao_confirmar(request: Request, token: str = Form(""),
                                   senha_instalacao: str = Form(""),
                                   db: Session = Depends(get_db)):
    """Põe o backup no lugar do banco vazio da máquina nova.

    Termina no LOGIN, não no painel: quem restaurou não tem sessão — o acesso
    válido daqui em diante é o dos gestores que vieram dentro do arquivo, com as
    senhas de sempre.
    """
    from app.services import instalacao

    fechada = _so_sem_gestor(db)
    if fechada is not None:
        return fechada
    # Conferida DE NOVO: a etapa 1 não deixa credencial de pé para a etapa 2 —
    # quem chegasse direto aqui com um token adivinhado passaria sem prova.
    if not instalacao.conferir_senha(senha_instalacao):
        return _tela_instalacao(request, erro=_ERRO_SENHA, status=400)
    try:
        bkp.caminho_envio(token)
    except bkp.ErroBackup as e:
        return _tela_instalacao(request, erro=str(e), status=400)

    db.close()
    try:
        feito = bkp.restaurar(token)
    except bkp.ErroBackup as e:
        return _tela_instalacao(request, erro=str(e), status=400)

    _registrar_restauracao(None, feito)
    # O backup traz os gestores dele: a senha de instalação cumpriu o papel.
    from app.services import instalacao as inst

    inst.encerrar_primeiro_acesso()
    return RedirectResponse("/gestao/login?ok=instalacao-restaurada", status_code=303)


def _registrar_restauracao(login: str | None, feito: bkp.Restauracao) -> None:
    """Grava no banco restaurado quem restaurou, e de que arquivo (regra 11).

    O gestor é procurado pelo LOGIN, não pelo id: os ids do banco restaurado são
    outros, e `auditoria.usuario_id` tem FK para `usuario`. Não achando o login
    lá dentro — que é o caso avisado na tela, e sempre o do primeiro acesso, onde
    não há login nenhum —, o registro fica sem autor em vez de não existir. Um
    ato que substitui a instalação inteira não pode passar sem deixar linha.
    """
    from sqlalchemy import select

    from app.database import SessionLocal

    nova = SessionLocal()
    try:
        autor = (nova.scalar(select(Usuario).where(Usuario.login == login))
                 if login else None)
        auditoria.registrar(
            nova, usuario_id=autor.id if autor else None, entidade="backup",
            acao="alterar",
            depois={
                "restaurado_por": login or "primeiro acesso (máquina nova)",
                "banco_do_backup": feito.retrato.revisao,
                "om_do_backup": feito.retrato.om,
                "servicos": feito.retrato.servicos,
                "militares": feito.retrato.militares,
                "copia_de_seguranca": (feito.copia_de_seguranca.name
                                       if feito.copia_de_seguranca else None),
            })
        nova.commit()
    finally:
        nova.close()
