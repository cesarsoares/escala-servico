"""Manual de uso (`/manual`) — aberto, sem login, e imprimível.

Por que Markdown em `docs/manual/manual.md` e não HTML nos templates:

  - quem corrige o texto é o gestor, não quem mexe em Jinja;
  - o mesmo arquivo se lê no repositório e na tela, sem duas versões
    divergindo;
  - o índice sai dos próprios títulos (extensão `toc`), então acrescentar uma
    seção não exige lembrar de atualizar o sumário.

Aberto porque o manual explica **também a consulta**, que é aberta (regra 13.1)
— exigir login para ler como se consulta a escala seria absurdo.

Impressão: o template usa `impressao.css`, a mesma folha do documento da escala
(preparada para impressora monocromática). O navegador salva em PDF; quando a
rota do WeasyPrint entrar, renderiza este mesmo HTML.
"""
from __future__ import annotations

from pathlib import Path

import markdown
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.web import templates

router = APIRouter(tags=["manual"])

ARQUIVO = Path(__file__).resolve().parents[2] / "docs" / "manual" / "manual.md"

_EXTENSOES = ["extra", "toc", "sane_lists"]
# O título da página vem do template (com a OM e a versão), então o arquivo
# começa direto no texto e as seções são `##`. Sem o recorte, o índice
# repetiria o próprio título como primeiro item.
_CONFIG = {"toc": {"toc_depth": "2-3"}}

# O manual não muda entre pedidos: converter a cada acesso seria trabalho puro.
# Guardado junto da hora de modificação, para o arquivo editado valer sem
# reiniciar o servidor (é o modo como se corrige um texto em produção).
_cache: tuple[float, str, str] | None = None


def _render() -> tuple[str, str]:
    """(corpo, índice) do manual em HTML. Recarrega se o arquivo mudou."""
    global _cache
    try:
        assinatura = ARQUIVO.stat().st_mtime
    except OSError:
        return ("<p>O manual não foi encontrado nesta instalação.</p>", "")
    if _cache is not None and _cache[0] == assinatura:
        return _cache[1], _cache[2]

    md = markdown.Markdown(extensions=_EXTENSOES, extension_configs=_CONFIG)
    corpo = md.convert(ARQUIVO.read_text(encoding="utf-8"))
    indice = getattr(md, "toc", "")
    _cache = (assinatura, corpo, indice)
    return corpo, indice


@router.get("/manual", response_class=HTMLResponse)
def manual(request: Request):
    corpo, indice = _render()
    return templates.TemplateResponse(request, "manual.html", {
        "corpo": corpo, "indice": indice,
    })
