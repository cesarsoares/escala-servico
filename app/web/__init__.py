"""Camada web (Jinja): instância de templates compartilhada.

Fica aqui (e não em main.py) para o router de gestão e o main importarem sem
import circular.
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app import VERSAO
from app.config import settings

WEB = Path(__file__).parent


def _identificacao_da_om(request: Request) -> dict[str, Any]:
    """Sigla/nome/suporte da OM em QUALQUER template, sem passar pelo contexto.

    Quem resolve é a dependência global `identificar_om` (main.py), que grava em
    `request.state` — assim a consulta ao banco usa a MESMA sessão do pedido e
    respeita o `dependency_overrides` dos testes. Sem `state` (erro antes da
    dependência rodar, ou template renderizado fora de um pedido), cai no
    `.env`: o cabeçalho não pode ficar vazio.
    """
    return {
        "om_sigla": getattr(request.state, "om_sigla", settings.om_sigla),
        "om_nome": getattr(request.state, "om_nome", settings.om_nome),
        "suporte_contato": getattr(request.state, "suporte_contato", ""),
    }


templates = Jinja2Templates(directory=str(WEB / "templates"),
                            context_processors=[_identificacao_da_om])


def hora_local(dt: datetime | None) -> str:
    """Carimbo de tempo do banco (UTC, naive) no fuso de quem lê a tela.

    `auditoria.criado_em` é gravado por `func.now()`, que no SQLite é **UTC** e
    sem tzinfo. Exibir o valor cru adianta o relógio (3h no horário de Brasília)
    — inaceitável num histórico de "quem mexeu e quando" (regra 11).

    `astimezone()` sem argumento usa o fuso do SERVIDOR, sem depender do pacote
    `tzdata` (que o Windows não traz). No container, o fuso vem do TZ do compose.
    """
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%d/%m/%Y %H:%M")


templates.env.filters["hora_local"] = hora_local

templates.env.globals["versao"] = VERSAO


# --- Confirmação de ação -----------------------------------------------------
# Toda ação de gestão termina em redirecionamento (padrão POST-redirect-GET, que
# evita regravar ao atualizar a página). O efeito colateral era o sistema NUNCA
# dizer que deu certo: só o erro tinha mensagem.
#
# A confirmação viaja como `?ok=<chave>` na URL do redirecionamento e é
# traduzida aqui. Assim não há sessão, cookie nem estado no servidor — e a
# mensagem é reproduzível: a mesma URL mostra o mesmo aviso.
AVISOS = {
    "militar-criado": "Militar cadastrado.",
    "militar-alterado": "Cadastro atualizado.",
    "militar-desativado": "Militar desativado — saiu da rotação; o histórico foi preservado.",
    "militar-reativado": "Militar reativado.",
    "impedimento-criado": "Impedimento registrado. Para refletir num mês já fechado, "
                          "re-escale o período com a opção 'regravar'.",
    "impedimento-removido": "Impedimento removido.",
    "escala-criada": "Escala criada. Falta incluir os participantes.",
    "escala-alterada": "Dados da escala salvos. Vale da próxima escalação em diante.",
    "escala-extinta": "Escala extinta — saiu da rotação; os serviços gravados foram preservados.",
    "escala-reativada": "Escala reativada.",
    "posto-criado": "Posto acrescentado.",
    "posto-alterado": "Rótulo do posto salvo.",
    "posto-removido": "Posto removido.",
    "participante-incluido": "Militar incluído na escala.",
    "participante-isento": "Militar isento desta escala — deixa de concorrer na fila.",
    "participante-cores": "Cores da participação salvas. Vale da próxima escalação "
                          "em diante (regra 3.3.1).",
    "concorrencia-criada": "Concorrência declarada. Vale nos dois sentidos.",
    "concorrencia-removida": "Concorrência desfeita.",
    "feriado-criado": "Feriado cadastrado.",
    "feriado-removido": "Feriado removido. Os dias já escalados não mudam.",
    "override-definido": "Cor do dia definida.",
    "override-removido": "Cor forçada removida — o dia volta à cor natural.",
    "permuta-criada": "Permuta registrada. A folga continua com quem estava escalado.",
    "permuta-cancelada": "Permuta cancelada — o escalado volta a figurar.",
    "primeiro-gestor": "Acesso criado e sessão iniciada. Anote a senha: "
                       "não há recuperação por e-mail.",
    # Configurações (regra 13.2 — uma instalação por OM)
    "configuracao-salva": "Configuração salva.",
    "om-criada": "OM cadastrada.",
    "om-alterada": "OM atualizada.",
    "om-excluida": "OM excluída.",
    "graduacao-criada": "Posto/graduação acrescentado.",
    "graduacao-alterada": "Posto/graduação atualizado.",
    "graduacao-movida": "Ordem hierárquica alterada. Vale da próxima escalação em "
                        "diante — os serviços já gravados não mudam.",
    "graduacao-ativada": "Posto/graduação reativado.",
    "graduacao-desativada": "Posto/graduação desativado — some dos formulários; "
                            "quem já o tem continua como está.",
    "graduacao-excluida": "Posto/graduação excluído.",
    "tipo-criado": "Tipo de impedimento acrescentado.",
    "tipo-ativado": "Tipo de impedimento reativado.",
    "tipo-desativado": "Tipo de impedimento desativado.",
    "tipo-excluido": "Tipo de impedimento excluído.",
    "gestor-criado": "Gestor cadastrado — já pode entrar com o login e a senha.",
    "senha-trocada": "Senha trocada.",
    "gestor-ativado": "Gestor reativado.",
    "gestor-desativado": "Gestor desativado — perdeu o acesso; o histórico dele fica.",
}
templates.env.globals["AVISOS"] = AVISOS

# Faixa aceita em ?ano= nas telas de mês. Sem isso, ano=0 estoura em
# date(ano, mes, 1) e devolve 500 — inclusive na consulta aberta (regra 13.1).
# Mora aqui pelo mesmo motivo dos templates: main.py e web/gestao.py precisam.
ANO_MIN = 1900
ANO_MAX = 2200
