"""Testes do desempate por antiguidade (seção 9)."""
from datetime import date

from app.domain.antiguidade import comparar_antiguidade
from app.domain.models import Militar


def _mil(id, posto, promo, praca=date(2010, 1, 1),
         numero_antiguidade=None, data_nascimento=None):
    return Militar(id, f"M{id}", f"Militar {id}", posto, "OM",
                   data_promocao=promo, data_praca=praca,
                   numero_antiguidade=numero_antiguidade,
                   data_nascimento=data_nascimento)


def test_posto_mais_moderno_vai_primeiro():
    # regra 9.1: Cap é mais moderno que Maj -> serve antes
    cap = _mil(1, "Cap", date(2022, 1, 1))
    maj = _mil(2, "Maj", date(2018, 1, 1))
    assert comparar_antiguidade(cap, maj) < 0


def test_mesma_graduacao_promocao_recente_primeiro():
    # regra 9.2: promovido mais recentemente é mais moderno
    novo = _mil(1, "Cap", date(2024, 1, 1))
    antigo = _mil(2, "Cap", date(2020, 1, 1))
    assert comparar_antiguidade(novo, antigo) < 0


def test_pracas_desempatam_por_numero_antiguidade():
    # regra 9.5 / art. 17 §1º: soldados de mesma graduação desempatam pelo número
    # de antiguidade da incorporação (maior nº = mais moderno = serve antes),
    # não pela promoção. Aqui o nº manda mesmo com promoções diferentes.
    moderno = _mil(1, "Sd", date(2023, 1, 1), numero_antiguidade=150)
    antigo = _mil(2, "Sd", date(2024, 1, 1), numero_antiguidade=100)
    assert comparar_antiguidade(moderno, antigo) < 0


def test_praca_sem_numero_cai_na_cadeia_padrao():
    # regra 9.5: se o número não foi informado, usa a cadeia normal (9.2).
    a = _mil(1, "Cb", date(2024, 1, 1))
    b = _mil(2, "Cb", date(2020, 1, 1))
    assert comparar_antiguidade(a, b) < 0


def test_desempate_final_por_data_nascimento():
    # art. 17: mais velho = mais antigo; logo o mais novo é o mais moderno.
    # Empate em graduação/promoção/praça -> decide o nascimento.
    novo = _mil(1, "Cap", date(2020, 1, 1), data_nascimento=date(1990, 1, 1))
    velho = _mil(2, "Cap", date(2020, 1, 1), data_nascimento=date(1985, 1, 1))
    assert comparar_antiguidade(novo, velho) < 0


def test_data_promocao_nula_nao_estoura_e_pula_criterio():
    # Efetivo da planilha entra sem datas de antiguidade. O desempate NÃO pode
    # estourar: o posto ainda decide (9.1) mesmo com data_promocao None.
    cap = _mil(1, "Cap", None)
    maj = _mil(2, "Maj", None)
    assert comparar_antiguidade(cap, maj) < 0        # Cap mais moderno que Maj


def test_um_lado_sem_promocao_cai_no_proximo_criterio():
    # Mesma graduação; só um tem data_promocao -> pula 9.2 e desempata pela praça.
    a = _mil(1, "Cap", None, praca=date(2015, 1, 1))
    b = _mil(2, "Cap", date(2020, 1, 1), praca=date(2012, 1, 1))
    assert comparar_antiguidade(a, b) < 0            # praça de a é mais recente


def test_tudo_nulo_resulta_empate():
    # Sem nenhum critério de data e sem ordem_manual -> empate (0), sem estourar.
    a = _mil(1, "Cap", None, praca=None)
    b = _mil(2, "Cap", None, praca=None)
    assert comparar_antiguidade(a, b) == 0
