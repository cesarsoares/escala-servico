"""Modelos de domínio (estruturas puras, independentes de banco).

Estes NÃO são as tabelas do banco (essas ficam em app/models/). São as
representações que o motor de escala manipula.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum


class Cor(str, Enum):
    """Regras 2.5 e 2.6."""
    PRETA = "preta"        # dias úteis
    VERMELHA = "vermelha"  # sábados, domingos e feriados


@dataclass(frozen=True)
class Militar:
    id: int
    nome_guerra: str
    nome_completo: str
    posto: str                 # ex.: "Cap", "Maj", "2º Sgt", "Sd"
    om: str
    # Posição do posto/graduação na escala hierárquica (art. 16) e se a
    # graduação é de praça (art. 17 §1º, regra 9.5). Vêm como DADO porque a
    # tabela de postos é editável pelo gestor: uma OM pode acrescentar ou
    # reordenar. Quando None, `antiguidade.py` cai na tabela da lei embutida —
    # é o que permite montar um Militar de domínio à mão (nos testes) sem
    # repetir a ordem em toda chamada.
    posto_ordem: int | None = None         # art. 16 (maior = mais antigo)
    posto_eh_praca: bool | None = None     # regra 9.5
    # Datas de antiguidade opcionais: o efetivo pode ser carregado da planilha
    # (que não as traz) e enriquecido depois pela ficha. Quando faltam, o
    # desempata (antiguidade.py) pula o critério em vez de estourar.
    data_promocao: date | None = None      # regra 9.2
    data_praca: date | None = None         # regra 9.3
    ordem_manual: int = 0      # desempate final do brigada (regra 9.4)
    numero_antiguidade: int | None = None  # regra 3.2.1/9.5 (praças; menor nº = mais antigo)
    data_nascimento: date | None = None    # art. 17 Lei 6.880/80 (mais velho = mais antigo)


@dataclass
class Participacao:
    """Vínculo militar <-> escala, com as duas datas que ordenam as filas."""
    militar: Militar
    escala_id: int
    ultima_preta: date | None = None      # regra 3.3
    ultima_vermelha: date | None = None
    ativo: bool = True                    # isenção permanente = ativo False (regra 7.6)

    def ultimo_na_cor(self, cor: Cor) -> date | None:
        return self.ultima_preta if cor is Cor.PRETA else self.ultima_vermelha


@dataclass(frozen=True)
class Escala:
    """Entidade criada/excluída pelo gestor (seção 4). Não há escalas fixas.

    - `postos`: nº de vagas por dia (regra 2.5). 1 = escala de oficial;
      vários = guarda do quartel.
    - `folga_minima_horas`: folga configurável (regra 7.2). None = default 48h;
      o piso rígido de 24h é garantido por `folga_horas()`.
    - `concorrentes`: ids das escalas concorrentes; relação simétrica declarada
      pelo gestor (regra 7.4.1). O caller mantém a simetria.
    - `ativa`: ciclo de vida (regra 4.4). Efetivo insuficiente -> gestor extingue.
    - `inicio_servico` / `duracao_horas`: janela do serviço (regra 2.4). Default
      08:00 por 24h, mas configurável (ex.: plantão 18:00 por 14h). A folga é
      medida do término ao início, então a janela entra no cálculo (seção 7).
    """
    id: int
    nome: str
    tem_preta: bool = True
    tem_vermelha: bool = True   # Museu: tem_preta=False (regra 4.5)
    postos: int = 1             # regra 2.5 / 4.2
    folga_minima_horas: int | None = None   # regra 7.2 (None = default 48h)
    concorrentes: frozenset[int] = frozenset()  # regra 7.4.1 (ids de escalas)
    ativa: bool = True          # regra 4.4
    inicio_servico: time = time(8, 0)   # regra 2.4 (default; configurável)
    duracao_horas: int = 24             # regra 2.4 (default; configurável)

    def folga_horas(self) -> int:
        """Folga mínima aplicável desta escala, com o piso de 24h (regra 7.2)."""
        from app.domain.folga import folga_efetiva_horas
        return folga_efetiva_horas(self.folga_minima_horas)

    def inicio_em(self, dia: date) -> datetime:
        """Instante de início do serviço que começa em `dia` (regra 2.4)."""
        return datetime.combine(dia, self.inicio_servico)

    def termino_em(self, dia: date) -> datetime:
        """Instante de término do serviço que começa em `dia` (regra 2.4)."""
        return self.inicio_em(dia) + timedelta(hours=self.duracao_horas)


@dataclass(frozen=True)
class Impedimento:
    """Dispensa, férias, curso, operação... período em que o militar não serve.

    Regra 7.5 — militar é pulado no período, mas mantém a vez.
    """
    militar_id: int
    inicio: date
    fim: date
    tipo: str = ""
    obs: str = ""

    def cobre(self, dia: date) -> bool:
        return self.inicio <= dia <= self.fim
