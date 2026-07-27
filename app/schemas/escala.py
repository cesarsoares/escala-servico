"""Schemas da escala e suas partes (seção 4).

Validações espelham as CheckConstraints do modelo:
  - folga_minima_horas: NULL (default 48h) ou >= 24h — piso rígido (regra 7.2);
  - duracao_horas > 0 (regra 2.4);
  - par de escalas concorrentes com ids distintos (regra 7.4.1).
"""
from __future__ import annotations

from datetime import time

from pydantic import Field, model_validator

from app.schemas.base import Entrada, Resposta


# --- Posto: cada vaga da escala num dia (regra 2.5) ---
class PostoCreate(Entrada):
    ordem: int = Field(ge=1)            # 1..N dentro da escala
    rotulo: str | None = Field(default=None, max_length=80)


class PostoOut(Resposta):
    id: int
    escala_id: int
    ordem: int
    rotulo: str | None


# --- Escala ---
class EscalaBase(Entrada):
    nome: str = Field(max_length=120)
    tem_preta: bool = True
    tem_vermelha: bool = True                       # Museu: tem_preta=False (regra 4.5)
    folga_minima_horas: int | None = Field(default=None, ge=24)  # regra 7.2 (NULL = 48h)
    inicio_servico: time = time(8, 0)               # regra 2.4
    duracao_horas: int = Field(default=24, gt=0)    # regra 2.4
    ativa: bool = True                              # regra 4.4

    @model_validator(mode="after")
    def _ao_menos_uma_cor(self):
        """Uma escala sem preta e sem vermelha não roda (regra 4.5)."""
        if not self.tem_preta and not self.tem_vermelha:
            raise ValueError("a escala precisa rodar ao menos uma cor (preta ou vermelha)")
        return self


class EscalaCreate(EscalaBase):
    # postos criados junto com a escala; ao menos um (regra 2.5)
    postos: list[PostoCreate] = Field(min_length=1)

    @model_validator(mode="after")
    def _ordens_unicas(self):
        ordens = [p.ordem for p in self.postos]
        if len(ordens) != len(set(ordens)):
            raise ValueError("a 'ordem' dos postos deve ser única dentro da escala")
        return self


class EscalaUpdate(Entrada):
    nome: str | None = Field(default=None, max_length=120)
    tem_preta: bool | None = None
    tem_vermelha: bool | None = None
    folga_minima_horas: int | None = Field(default=None, ge=24)
    inicio_servico: time | None = None
    duracao_horas: int | None = Field(default=None, gt=0)
    ativa: bool | None = None

    @model_validator(mode="after")
    def _nao_zerar_as_duas_cores(self):
        """Pega o caso óbvio de desligar as duas cores na mesma requisição
        (regra 4.5). O merge com o estado atual é revalidado no service; o piso
        real é a CheckConstraint ck_ao_menos_uma_cor."""
        if self.tem_preta is False and self.tem_vermelha is False:
            raise ValueError("a escala precisa rodar ao menos uma cor (preta ou vermelha)")
        return self


class EscalaOut(Resposta):
    id: int
    nome: str
    tem_preta: bool
    tem_vermelha: bool
    folga_minima_horas: int | None
    inicio_servico: time
    duracao_horas: int
    ativa: bool


class EscalaDetalheOut(EscalaOut):
    """Escala com seus postos (para a tela de configuração)."""
    postos: list[PostoOut]


# --- Participação: vínculo militar <-> escala (regra 3.3) ---
class ParticipacaoCreate(Entrada):
    militar_id: int
    escala_id: int
    ativo: bool = True   # isenção permanente = não-participação (regra 7.6)


class ParticipanteIn(Entrada):
    """Adiciona um militar à escala (a escala vem do path)."""
    militar_id: int


class ParticipacaoOut(Resposta):
    id: int
    militar_id: int
    escala_id: int
    ativo: bool


# --- Concorrência: relação explícita e simétrica (regra 7.4.1) ---
class EscalaConcorrenteCreate(Entrada):
    """Declara duas escalas como concorrentes. A ordem dos ids é indiferente;
    a camada de serviço normaliza para menor<maior antes de gravar."""
    escala_a_id: int
    escala_b_id: int

    @model_validator(mode="after")
    def _distintas(self):
        if self.escala_a_id == self.escala_b_id:
            raise ValueError("uma escala não concorre consigo mesma")
        return self


class EscalaConcorrenteOut(Resposta):
    escala_menor_id: int
    escala_maior_id: int


class ConcorrenteIn(Entrada):
    """Declara a escala do path como concorrente de `escala_b_id`."""
    escala_b_id: int
