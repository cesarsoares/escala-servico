"""Schemas de serviço (militar-dia-posto) e permuta.

O serviço normalmente é PRODUZIDO pelo motor (services), não digitado. `cor`,
`inicio_dt` e `termino_dt` derivam do calendário e da janela da escala — por isso
o Create carrega só o essencial e a camada de serviço calcula o resto.
A permuta é registro puro: a folga NUNCA muda de dono (regra 9).
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from app.domain.models import Cor
from app.schemas.base import Entrada, Resposta
from app.schemas.militar import MilitarResumoOut


# --- Serviço ---
class ServicoCreate(Entrada):
    """Escalação manual de uma vaga num dia. `cor`/`inicio_dt`/`termino_dt` são
    derivados na camada de serviço (calendário + janela da escala)."""
    escala_id: int
    posto_id: int
    militar_id: int
    dia: date


class ServicoOut(Resposta):
    id: int
    escala_id: int
    posto_id: int
    militar_id: int
    dia: date
    cor: Cor
    inicio_dt: datetime
    termino_dt: datetime


class ServicoDetalheOut(ServicoOut):
    """Serviço com o militar escalado aninhado (consulta/PDF)."""
    militar: MilitarResumoOut


# --- Permuta (regra 9) ---
class PermutaCreate(Entrada):
    """Registro de troca. `autorizado_por` vem da sessão do gestor, não do corpo."""
    servico_id: int
    militar_substituto_id: int
    observacao: str | None = Field(default=None, max_length=200)


class PermutaOut(Resposta):
    id: int
    servico_id: int
    militar_substituto_id: int
    autorizado_por: int | None
    criado_em: datetime
    observacao: str | None


# --- Escalação de período (dispara o motor — regras 6/7) ---
class EscalacaoPedido(Entrada):
    """Fecha a escala de `inicio` a `fim` (inclusive), gravando os serviços."""
    inicio: date
    fim: date


class EscalacaoDiaOut(Resposta):
    """Resumo de um dia escalado: quem serve e o aviso de efetivo (regra 7.8)."""
    dia: date
    cor: Cor
    militar_ids: list[int]
    postos_solicitados: int
    efetivo_insuficiente: bool


class EscalacaoOut(Resposta):
    """Retrato do período fechado, com a contagem de dias em alerta (regra 7.8)."""
    escala_id: int
    inicio: date
    fim: date
    dias: list[EscalacaoDiaOut]
    dias_com_alerta: int
