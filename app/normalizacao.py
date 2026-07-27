"""Normalização de identificadores (CPF e identidade militar).

Mora na raiz de `app/` porque as três portas de entrada do cadastro precisam da
MESMA regra: a ficha em PDF (`services/ficha.py`), a API (`schemas/militar.py`)
e o formulário da tela de gestão (`web/gestao.py`).

Por que importa: a ficha grava só dígitos. Se o formulário aceitar a máscara,
`605.126.360-87` e `60512636087` são strings diferentes — passam pelo UNIQUE e
pela checagem de duplicata, e a mesma pessoa acaba com dois cadastros
concorrendo na fila da escala.
"""
from __future__ import annotations

import re


def so_digitos(texto: str | None) -> str | None:
    """Só os dígitos; mantém zero à esquerda (por isso a coluna é varchar).

    Devolve None para vazio/None. Não valida o CPF (dígito verificador): o
    cadastro aceita o que a ficha trouxer.
    """
    if not texto:
        return None
    return re.sub(r"\D", "", texto) or None
