"""Feriados nacionais brasileiros para um ano (regra 5.2).

Cobre os feriados fixos (Lei 662/49 e 6.802/80) e os móveis derivados da Páscoa
(Sexta-feira Santa, Carnaval, Corpus Christi). O gestor adiciona feriados locais.

A data da Páscoa é calculada pelo algoritmo de Gauss/Meeus (calendário gregoriano).
"""
from __future__ import annotations

from datetime import date, timedelta


def domingo_de_pascoa(ano: int) -> date:
    """Domingo de Páscoa (algoritmo 'Anonymous Gregorian' / Meeus)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    mes = (h + m - 7 * n + 114) // 31
    dia = ((h + m - 7 * n + 114) % 31) + 1
    return date(ano, mes, dia)


def feriados_nacionais(ano: int) -> dict[date, str]:
    """Mapa {data: nome} dos feriados nacionais do ano."""
    pascoa = domingo_de_pascoa(ano)
    fixos = {
        date(ano, 1, 1): "Confraternização Universal",
        date(ano, 4, 21): "Tiradentes",
        date(ano, 5, 1): "Dia do Trabalho",
        date(ano, 9, 7): "Independência do Brasil",
        date(ano, 10, 12): "Nossa Senhora Aparecida",
        date(ano, 11, 2): "Finados",
        date(ano, 11, 15): "Proclamação da República",
        date(ano, 12, 25): "Natal",
    }
    if ano >= 2024:  # Lei 14.759/2023
        fixos[date(ano, 11, 20)] = "Dia Nacional de Zumbi e da Consciência Negra"
    moveis = {
        pascoa - timedelta(days=48): "Carnaval (segunda)",
        pascoa - timedelta(days=47): "Carnaval (terça)",
        pascoa - timedelta(days=2): "Sexta-feira Santa",
        pascoa + timedelta(days=60): "Corpus Christi",
    }
    return {**fixos, **moveis}
