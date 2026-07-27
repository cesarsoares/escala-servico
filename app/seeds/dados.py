"""Dados fixos usados no seed.

Círculos e postos/graduações vêm da Lei 6.880/80 (art. 16); a `ordem_hierarquica`
reaproveita o POSTO_ORDEM do domínio (app/domain/antiguidade.py). Praças
especiais (Asp Of, Cad) foram alocadas no círculo "Oficiais Subalternos" por
decisão do gestor — não usam o desempate 9.5 (eh_praca=False).
"""
from __future__ import annotations

# (nome, ordem, eh_praca) — maior ordem = mais antigo (art. 16)
CIRCULOS: list[tuple[str, int, bool]] = [
    ("Oficiais-Generais", 6, False),
    ("Oficiais Superiores", 5, False),
    ("Oficiais Intermediários", 4, False),
    ("Oficiais Subalternos", 3, False),
    ("Subtenentes e Sargentos", 2, True),
    ("Cabos e Soldados", 1, True),
]

# (sigla, nome, ordem_hierarquica, nome_do_circulo)
POSTOS_GRADUACAO: list[tuple[str, str, int, str]] = [
    ("Gen Ex", "General de Exército", 32, "Oficiais-Generais"),
    ("Gen Div", "General de Divisão", 31, "Oficiais-Generais"),
    ("Gen Bda", "General de Brigada", 30, "Oficiais-Generais"),
    ("Cel", "Coronel", 26, "Oficiais Superiores"),
    ("Ten Cel", "Tenente-Coronel", 25, "Oficiais Superiores"),
    ("Maj", "Major", 24, "Oficiais Superiores"),
    ("Cap", "Capitão", 23, "Oficiais Intermediários"),
    ("1º Ten", "Primeiro-Tenente", 22, "Oficiais Subalternos"),
    ("2º Ten", "Segundo-Tenente", 21, "Oficiais Subalternos"),
    # Praças especiais (art. 19) no círculo de oficiais subalternos (decisão do gestor)
    ("Asp Of", "Aspirante a Oficial", 20, "Oficiais Subalternos"),
    ("Cad", "Cadete", 19, "Oficiais Subalternos"),
    ("S Ten", "Subtenente", 14, "Subtenentes e Sargentos"),
    ("1º Sgt", "Primeiro-Sargento", 13, "Subtenentes e Sargentos"),
    ("2º Sgt", "Segundo-Sargento", 12, "Subtenentes e Sargentos"),
    ("3º Sgt", "Terceiro-Sargento", 11, "Subtenentes e Sargentos"),
    ("Cb", "Cabo", 9, "Cabos e Soldados"),
    ("Sd", "Soldado", 8, "Cabos e Soldados"),
]

# Tipos de impedimento mais comuns (regra 7.5). O gestor pode criar outros.
TIPOS_IMPEDIMENTO: list[str] = [
    "Dispensa",
    "Férias",
    "Curso",
    "Operação",
    "Missão",
    "Licença médica",
]
