"""Desempate por antiguidade (seção 9 das regras; Lei 6.880/80, art. 16-17).

'Mais moderno' vai primeiro na fila de serviço (regra 6.2 — convenção da escala,
não do Estatuto: o Estatuto só define quem é mais antigo). A cadeia é:
  9.1 posto/graduação (mais moderno = menos graduado)      -> art. 16 (escala hierárquica)
  9.5 praças de tropa: número de antiguidade da incorporação (regra 3.2.1) -> art. 17 §1º
  9.2 data de promoção (mais recente = mais moderno)
  9.3 data de praça (mais recente = mais moderno)
  9.6 data de nascimento (mais novo = mais moderno)         -> art. 17 (mais velho = mais antigo)
  9.4 decisão manual do brigada (campo ordem_manual)

Para praças de tropa, o desempate da graduação é o número de antiguidade dado
pela OM na incorporação (regras 3.2.1/9.5; art. 17 §1º), e NÃO a promoção/praça.
"""
from __future__ import annotations

from functools import cmp_to_key

from app.domain.models import Militar

# Escala hierárquica do Exército (Lei 6.880/80, art. 16 e Quadro anexo).
# MAIOR índice = mais antigo (mais graduado); menor = mais moderno.
#
# ATENÇÃO — isto é o VALOR DE LEI, usado só como último recurso. A ordem que
# vale em produção vem da tabela `posto_graduacao`, que o gestor edita em
# Configurações, e chega aqui em `Militar.posto_ordem` (preenchido por
# `services/mapeamento.militar_para_dominio`). Se este dicionário voltar a ser
# a fonte da verdade, uma OM que reordene a tabela passa a desempatar a fila
# com a ordem errada, em silêncio.
POSTO_ORDEM: dict[str, int] = {
    # Oficiais-generais
    "Gen Ex": 32, "Gen Div": 31, "Gen Bda": 30,
    # Oficiais superiores
    "Cel": 26, "Ten Cel": 25, "Maj": 24,
    # Oficial intermediário
    "Cap": 23,
    # Oficiais subalternos
    "1º Ten": 22, "2º Ten": 21,
    # Praças especiais (art. 19 — acima das demais praças)
    "Asp Of": 20, "Cad": 19,
    # Graduados
    "S Ten": 14, "1º Sgt": 13, "2º Sgt": 12, "3º Sgt": 11,
    # Praças de tropa
    "Cb": 9, "Sd": 8,
}

# Graduações cujo desempate usa o número de antiguidade da incorporação
# (regras 3.2.1/9.5), quando informado, em vez da cadeia de promoção/praça.
# Mesmo caso do POSTO_ORDEM: em produção vem de `Militar.posto_eh_praca`
# (círculo hierárquico da tabela); esta lista é só o valor de lei de reserva.
GRADUACOES_PRACA: frozenset[str] = frozenset({
    "S Ten", "1º Sgt", "2º Sgt", "3º Sgt", "Cb", "Sd",
})


def _ordem_posto(m: Militar) -> int:
    """Posição do posto/graduação: a informada pelo chamador, senão a da lei.

    O gestor edita a tabela de postos em Configurações, então a ordem é DADO —
    quem monta o `Militar` de domínio a traz junto (regra 9.1, art. 16).
    """
    if m.posto_ordem is not None:
        return m.posto_ordem
    return POSTO_ORDEM.get(m.posto, 999)


def _eh_praca(m: Militar) -> bool:
    """Se o desempate da graduação é o número de antiguidade (regra 9.5)."""
    if m.posto_eh_praca is not None:
        return m.posto_eh_praca
    return m.posto in GRADUACOES_PRACA


def comparar_antiguidade(a: Militar, b: Militar) -> int:
    """<0 se `a` vem antes de `b` (a é mais moderno); >0 se depois; 0 se igual."""
    oa, ob = _ordem_posto(a), _ordem_posto(b)
    if oa != ob:                      # 9.1: menor índice = mais moderno = primeiro
        return -1 if oa < ob else 1

    # 9.5 (art. 17 §1º): praças de tropa de mesma graduação desempatam pelo
    # número de antiguidade da incorporação; maior número = mais moderno.
    if _eh_praca(a) and a.numero_antiguidade is not None \
            and b.numero_antiguidade is not None:
        if a.numero_antiguidade != b.numero_antiguidade:
            return -1 if a.numero_antiguidade > b.numero_antiguidade else 1

    # 9.2/9.3: promoção e praça mais recentes = mais moderno. Só desempatam se
    # ambos os lados têm a data (efetivo vindo da planilha pode não ter — nesse
    # caso pula-se o critério em vez de estourar).
    if a.data_promocao is not None and b.data_promocao is not None \
            and a.data_promocao != b.data_promocao:
        return -1 if a.data_promocao > b.data_promocao else 1
    if a.data_praca is not None and b.data_praca is not None \
            and a.data_praca != b.data_praca:
        return -1 if a.data_praca > b.data_praca else 1

    # 9.6 (art. 17): mais velho = mais antigo; logo o mais novo é o mais moderno.
    if a.data_nascimento is not None and b.data_nascimento is not None \
            and a.data_nascimento != b.data_nascimento:
        return -1 if a.data_nascimento > b.data_nascimento else 1

    if a.ordem_manual != b.ordem_manual:    # 9.4: decisão do brigada
        return -1 if a.ordem_manual < b.ordem_manual else 1
    return 0


chave_antiguidade = cmp_to_key(comparar_antiguidade)
