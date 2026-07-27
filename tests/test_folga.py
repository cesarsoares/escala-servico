"""Testes da folga mínima configurável, do piso de 24h e da janela (seção 7)."""
from datetime import date, datetime, time

from app.domain.folga import folga_efetiva_horas, respeita_folga_minima
from app.domain.models import Escala

# Escala padrão: 24h a partir das 08:00 (regra 2.4).
PADRAO = Escala(1, "Oficial de Dia")

QUI = date(2026, 7, 16)   # serviço na quinta (08:00 qui -> 08:00 sex)
SEX = date(2026, 7, 17)   # término do serviço da quinta
SAB = date(2026, 7, 18)   # +24h do término
DOM = date(2026, 7, 19)   # +48h do término

TERMINO_QUI = PADRAO.termino_em(QUI)   # sexta 08:00


def test_quem_nunca_serviu_esta_disponivel():
    assert respeita_folga_minima(None, PADRAO.inicio_em(QUI)) is True


# --- Default de 48h (escalas de oficial) ---

def test_sabado_nao_respeita_o_piso_de_48h():
    # regra 7.3: serviço quinta -> ainda em folga no sábado (só 24h)
    assert respeita_folga_minima(TERMINO_QUI, PADRAO.inicio_em(SAB)) is False


def test_domingo_respeita_o_piso_de_48h():
    # regra 7.3: disponível no domingo (folga sexta e sábado)
    assert respeita_folga_minima(TERMINO_QUI, PADRAO.inicio_em(DOM)) is True


# --- Folga configurável (regra 7.2.1) ---

def test_folga_de_24h_libera_no_dia_seguinte_ao_termino():
    # regra 7.2.1: guarda com folga de 24h -> serviço quinta libera no sábado
    assert respeita_folga_minima(TERMINO_QUI, PADRAO.inicio_em(SAB), 24) is True


# --- Piso rígido de 24h (regra 7.2.2) ---

def test_piso_rigido_nunca_abaixo_de_24h():
    # configurar 12h é elevado ao piso de 24h -> sábado (24h) passa
    assert respeita_folga_minima(TERMINO_QUI, PADRAO.inicio_em(SAB), 12) is True


def test_nao_assume_no_mesmo_dia_em_que_saiu():
    # regra 7.2.2: término sexta 08:00 -> não assume outro serviço na sexta
    assert respeita_folga_minima(TERMINO_QUI, PADRAO.inicio_em(SEX), 24) is False


def test_folga_efetiva_aplica_piso_e_default():
    assert folga_efetiva_horas(12) == 24    # piso
    assert folga_efetiva_horas(72) == 72    # configurado acima do piso
    assert folga_efetiva_horas(None) == 48  # default sugerido


def test_escala_folga_horas_usa_piso_e_default():
    assert Escala(1, "Reforço", folga_minima_horas=12).folga_horas() == 24
    assert Escala(1, "Guarda", folga_minima_horas=24).folga_horas() == 24
    assert Escala(1, "Oficial de Dia").folga_horas() == 48


# --- Janela de serviço configurável (regras 2.4 / 4.2) ---

def test_janela_padrao_e_08h_por_24h():
    assert PADRAO.inicio_em(QUI) == datetime(2026, 7, 16, 8, 0)
    assert PADRAO.termino_em(QUI) == datetime(2026, 7, 17, 8, 0)


def test_janela_configuravel_18h_por_14h():
    # plantão que começa às 18:00 e vai até as 08:00 do dia seguinte (14h)
    plantao = Escala(2, "Plantão", inicio_servico=time(18, 0), duracao_horas=14)
    assert plantao.inicio_em(QUI) == datetime(2026, 7, 16, 18, 0)
    assert plantao.termino_em(QUI) == datetime(2026, 7, 17, 8, 0)


def test_folga_cruza_escalas_de_janelas_diferentes():
    # término do plantão (17/08:00) vs início do serviço padrão no dia 17 (08:00)
    # regra 7.4.2 + piso 24h: gap 0h -> não assume no mesmo dia
    plantao = Escala(2, "Plantão", inicio_servico=time(18, 0), duracao_horas=14)
    termino_plantao = plantao.termino_em(QUI)          # sexta 08:00
    assert respeita_folga_minima(termino_plantao, PADRAO.inicio_em(SEX), 24) is False
    # sábado 08:00: gap 24h -> libera com folga de 24h
    assert respeita_folga_minima(termino_plantao, PADRAO.inicio_em(SAB), 24) is True
