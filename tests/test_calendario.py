"""Testes da classificação do dia (seção 5)."""
from datetime import date

from app.domain.calendario import classificar_dia
from app.domain.models import Cor

SAB = date(2026, 7, 18)   # sábado
DOM = date(2026, 7, 19)   # domingo
QUI = date(2026, 7, 16)   # quinta (dia útil)
SEX = date(2026, 7, 17)   # sexta (dia útil)


def test_fim_de_semana_e_vermelha():
    assert classificar_dia(SAB, feriados=set()) is Cor.VERMELHA
    assert classificar_dia(DOM, feriados=set()) is Cor.VERMELHA


def test_dia_util_e_preta():
    assert classificar_dia(QUI, feriados=set()) is Cor.PRETA


def test_feriado_vira_vermelha():
    assert classificar_dia(QUI, feriados={QUI}) is Cor.VERMELHA


def test_override_do_comando_vira_vermelha():
    # regra 5.3: comando declara a sexta como vermelha
    assert classificar_dia(SEX, feriados=set(), override_vermelha={SEX}) is Cor.VERMELHA


def test_override_preta_vence_fim_de_semana_e_feriado():
    # regra 5.3: comando força um feriado/sábado como dia útil (preta)
    assert classificar_dia(SAB, feriados=set(), override_preta={SAB}) is Cor.PRETA
    assert classificar_dia(QUI, feriados={QUI}, override_preta={QUI}) is Cor.PRETA
