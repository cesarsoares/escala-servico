"""Tela do reajuste automático da escala (item 2 do Brigada, 01/08).

O reajuste acontece SOZINHO, dentro da transação da alteração que o disparou
(uma dispensa, um serviço corrigido, uma isenção). Isso cria um problema de
apresentação: o gestor não pediu nada, e mesmo assim a escala mudou de quem
serve em vários dias. Ele precisa ver o que mudou — e o "antes" **não existe
mais em lugar nenhum** depois de gravado.

Por isso o retrato vai para a AUDITORIA (regra 11, onde a mudança já teria de
ser registrada de qualquer forma) e a rota redireciona para cá com o id do
registro. Vantagens sobre renderizar o resultado direto no POST: o padrão
POST-redirect-GET continua valendo (atualizar a página não regrava nada) e o
gestor pode voltar a esta URL semanas depois, pelo Histórico.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.gestao import Auditoria, Usuario
from app.services.reajuste import ENTIDADE
from app.web import templates
from app.web.gestao import gestor_web

router = APIRouter(prefix="/gestao", tags=["gestão"])

# Por que a escala foi reajustada — texto fixo, escolhido pela rota que
# disparou. Fica em dicionário pelo mesmo motivo do AVISOS: é o sistema que
# escreve na tela, nunca a URL.
ORIGENS = {
    "impedimento-criado": "Você registrou um impedimento (dispensa, férias, curso).",
    "impedimento-removido": "Você removeu um impedimento.",
    "servico-lancado": "Você lançou um serviço à mão.",
    "servico-alterado": "Você corrigiu um serviço.",
    "servico-removido": "Você apagou um serviço.",
    "participante-isento": "Você isentou um militar desta escala.",
    "participante-incluido": "Você incluiu um militar na escala.",
    "militar-desativado": "Você desativou um militar.",
    "militar-reativado": "Você reativou um militar.",
}


def _dia(texto: str) -> date:
    return date.fromisoformat(texto)


@router.get("/reajuste/{auditoria_id}", response_class=HTMLResponse)
def ver_reajuste(
    auditoria_id: int,
    request: Request,
    db: Session = Depends(get_db),
    gestor: Usuario = Depends(gestor_web),
):
    """O que o sistema mudou sozinho, a partir do retrato guardado na auditoria."""
    reg = db.get(Auditoria, auditoria_id)
    if reg is None or reg.entidade != ENTIDADE:
        return RedirectResponse("/gestao?falha=reajuste-inexistente", status_code=303)

    dados = reg.dados_depois or {}
    reajustes = []
    for r in dados.get("reajustes", []):
        reajustes.append({
            "escala": r.get("escala", ""),
            "escala_id": r.get("escala_id"),
            "inicio": _dia(r["inicio"]) if r.get("inicio") else None,
            "fim": _dia(r["fim"]) if r.get("fim") else None,
            "dias_processados": r.get("dias_processados", 0),
            "alterados": [
                {"dia": _dia(d["dia"]), "antes": d.get("antes", []),
                 "depois": d.get("depois", []), "no_boletim": d.get("no_boletim", False)}
                for d in r.get("alterados", [])
            ],
            "pulados": [_dia(d) for d in r.get("pulados_por_permuta", [])],
            "descobertos": [_dia(d) for d in r.get("descobertos", [])],
        })
    total_boletim = sum(
        1 for r in reajustes for d in r["alterados"] if d["no_boletim"]
    )
    return templates.TemplateResponse(request, "gestao/reajuste.html", {
        "gestor": gestor,
        "quando": reg.criado_em,
        "origem": ORIGENS.get(dados.get("origem", ""), "Uma alteração mudou a fila."),
        "reajustes": reajustes,
        "total_boletim": total_boletim,
    })
