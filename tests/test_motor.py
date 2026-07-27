"""Testes do motor de rotação (seção 6)."""
from datetime import date

from app.domain.models import Cor, Escala, Impedimento, Militar, Participacao
from app.domain.motor import fila_ordenada, proximo, proximos

# Escala padrão: 1 posto, folga 48h, janela 08:00/24h.
ESC = Escala(1, "Teste")


def _esc(postos=1, folga=None):
    return Escala(1, "Teste", postos=postos, folga_minima_horas=folga)


def _part(id, ultima_preta):
    m = Militar(id, f"M{id}", f"Militar {id}", "2º Sgt", "OM",
                data_promocao=date(2020, 1, 1), data_praca=date(2010, 1, 1))
    return Participacao(m, escala_id=1, ultima_preta=ultima_preta)


def test_mais_folgado_vai_primeiro():
    # quem serviu há mais tempo (data mais antiga) fica no topo (regra 6.2)
    p_recente = _part(1, date(2026, 1, 10))
    p_antigo = _part(2, date(2026, 1, 5))
    fila = fila_ordenada([p_recente, p_antigo], Cor.PRETA)
    assert fila[0].militar.id == 2


def test_pula_quem_esta_impedido_mas_nao_muda_a_fila():
    # regra 6.4/7.5: impedido é pulado; assume o próximo
    dia = date(2026, 1, 20)
    p_antigo = _part(2, date(2026, 1, 5))     # seria o primeiro
    p_recente = _part(1, date(2026, 1, 10))
    imped = [Impedimento(militar_id=2, inicio=dia, fim=dia, tipo="DM")]
    escolhido = proximo(ESC, [p_recente, p_antigo], Cor.PRETA, dia, imped,
                        ultimo_termino_por_militar={})
    assert escolhido.militar.id == 1


def test_proximos_pega_os_n_mais_folgados_para_os_postos():
    # regra 6.1: escala com 3 postos -> tira os 3 mais folgados da fila
    dia = date(2026, 2, 1)  # longe dos serviços de janeiro: todos disponíveis
    ps = [_part(1, date(2026, 1, 10)), _part(2, date(2026, 1, 5)),
          _part(3, date(2026, 1, 8)), _part(4, date(2026, 1, 12))]
    escolhidos = proximos(_esc(postos=3), ps, Cor.PRETA, dia, [],
                          ultimo_termino_por_militar={})
    assert [p.militar.id for p in escolhidos] == [2, 3, 1]


def test_proximos_efetivo_insuficiente_retorna_menos_que_n():
    # regra 7.8: menos disponíveis que postos -> lista menor que N (aviso ao gestor)
    dia = date(2026, 2, 1)
    ps = [_part(1, date(2026, 1, 10)), _part(2, date(2026, 1, 5))]
    imped = [Impedimento(militar_id=2, inicio=dia, fim=dia, tipo="ferias")]
    escolhidos = proximos(_esc(postos=3), ps, Cor.PRETA, dia, imped,
                          ultimo_termino_por_militar={})
    assert [p.militar.id for p in escolhidos] == [1]


def test_concorrencia_bloqueia_pelo_ultimo_termino_global():
    # regra 7.4: mais folgado na fila, mas serviu numa escala concorrente e
    # ainda está em folga (default 48h) -> é pulado.
    dia = date(2026, 1, 20)
    p1 = _part(1, date(2026, 1, 5))    # topo da fila desta escala
    p2 = _part(2, date(2026, 1, 10))
    # serviço concorrente iniciado em 19 -> término 20/08:00 (janela padrão)
    ultimo = {1: ESC.termino_em(date(2026, 1, 19))}
    escolhido = proximo(ESC, [p1, p2], Cor.PRETA, dia, [], ultimo)
    assert escolhido.militar.id == 2


def test_folga_configurada_de_24h_libera_mais_cedo():
    # regra 7.4.2: o piso aplicado é o da escala de destino
    dia = date(2026, 1, 20)
    p1 = _part(1, date(2026, 1, 5))
    # serviço concorrente iniciado em 18 -> término 19/08:00; até 20/08:00 = 24h
    ultimo = {1: ESC.termino_em(date(2026, 1, 18))}
    # escala de destino com 48h: bloqueado
    assert proximo(_esc(folga=None), [p1], Cor.PRETA, dia, [], ultimo) is None
    # escala de destino com 24h: liberado
    escolhido = proximo(_esc(folga=24), [p1], Cor.PRETA, dia, [], ultimo)
    assert escolhido.militar.id == 1
