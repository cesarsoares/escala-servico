"""Tela de IMPORTAÇÃO do histórico de serviços em CSV (protegida — regra 11).

Uma OM que instala o sistema hoje já tem passado: quem serviu em que dia está
numa planilha. Sem carregá-lo, o motor começa com todo mundo empatado em "nunca
serviu" e a primeira escalação sai só pela antiguidade, ignorando quem acabou
de deixar o serviço.

Duas etapas, como na importação da ficha em PDF:

  1. **Conferir** — o arquivo é lido e confrontado com o banco; a tela mostra o
     que entra, o que entra com ressalva e o que foi recusado, com o motivo e o
     número da linha. Nada é gravado.
  2. **Confirmar** — o mesmo conteúdo é relido e gravado, tudo ou nada.

O conteúdo viaja de uma etapa para a outra num campo oculto, e não numa sessão
no servidor: é o mesmo princípio das confirmações por `?ok=` — sem estado, o
que se confirma é exatamente o que se conferiu. A releitura na etapa 2 é
deliberada: entre uma e outra, alguém pode ter gravado o dia pela tela de
escalação, e a segunda leitura pega isso.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.gestao import Usuario
from app.services import auditoria
from app.services import importacao_csv as csv_service
from app.web import templates
from app.web.gestao import gestor_web

router = APIRouter(prefix="/gestao", tags=["web-gestão"])


def _tela(request: Request, gestor: Usuario, leitura=None, conteudo: str = "",
          erro: str | None = None, status: int = 200):
    return templates.TemplateResponse(request, "gestao/importar.html", {
        "gestor": gestor, "leitura": leitura, "conteudo": conteudo, "erro": erro,
        "colunas": csv_service.COLUNAS,
    }, status_code=status)


@router.get("/importar", response_class=HTMLResponse)
def importar(request: Request, gestor: Usuario = Depends(gestor_web)):
    return _tela(request, gestor)


@router.get("/importar/modelo.csv")
def modelo(db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    """Modelo já preenchido com as escalas e o efetivo desta instalação."""
    corpo = csv_service.modelo(db)
    return Response(
        # BOM para o Excel abrir o acento certo com dois cliques.
        content=corpo.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="modelo-servicos.csv"'},
    )


@router.post("/importar", response_class=HTMLResponse)
async def conferir(request: Request, arquivo: UploadFile | None = File(default=None),
                   db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    """Etapa 1: lê e confronta com o banco. Não grava nada."""
    if arquivo is None or not arquivo.filename:
        return _tela(request, gestor, erro="Escolha o arquivo .csv.", status=400)
    conteudo = await arquivo.read()
    leitura = csv_service.ler(db, conteudo)
    if leitura.erro_geral:
        return _tela(request, gestor, leitura, erro=leitura.erro_geral, status=400)
    return _tela(request, gestor, leitura,
                 conteudo=csv_service.decodificar(conteudo))


@router.post("/importar/confirmar")
def confirmar(request: Request, conteudo: str = Form(""),
              db: Session = Depends(get_db), gestor: Usuario = Depends(gestor_web)):
    """Etapa 2: relê o mesmo conteúdo e grava as linhas aceitas."""
    leitura = csv_service.ler(db, conteudo.encode("utf-8"))
    if leitura.erro_geral:
        return _tela(request, gestor, leitura, conteudo, leitura.erro_geral, status=400)
    if not leitura.aceitas:
        return _tela(request, gestor, leitura, conteudo,
                     erro="Nenhuma linha aceita — nada foi gravado.", status=400)

    criados = csv_service.aplicar(db, leitura)
    auditoria.registrar(
        db, usuario_id=gestor.id, entidade="servico", entidade_id=None,
        acao="criar", antes=None,
        depois={
            "origem": "importação CSV",
            "servicos_criados": criados,
            "linhas_recusadas": len(leitura.recusadas),
            "linhas_com_aviso": len(leitura.com_aviso),
        })
    db.commit()
    return RedirectResponse(f"/gestao/importar?ok=importacao-concluida&n={criados}",
                            status_code=303)
