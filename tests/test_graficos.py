"""Cálculos por trás dos gráficos das telas de gestão.

As barras são DADO: largura errada mente com aparência de precisão. Aqui se
fixa a aritmética — as telas só formatam o que estes cálculos devolvem.
"""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.domain.models import Cor
from app.models.escala import Escala, Participacao, Posto
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.models.servico import Servico
from app.seeds import seed_circulos, seed_postos_graduacao, seed_tipos_impedimento
from app.services import painel

HOJE = date(2026, 8, 3)


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = Session(engine)
    seed_circulos(s)
    seed_postos_graduacao(s)
    seed_tipos_impedimento(s)
    s.add(OrganizacaoMilitar(id=1, nome="Quartel General", sigla="QG"))
    s.flush()
    sgt = s.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "2º Sgt"))
    for i in (1, 2, 3, 4):
        s.add(Militar(id=i, nome_guerra=f"M{i}", nome_completo=f"Militar {i}",
                      posto_graduacao_id=sgt, om_id=1, numero_antiguidade=100 - i))
    s.add(Escala(id=1, nome="Oficial de Dia", folga_minima_horas=24))
    s.flush()
    s.add(Posto(id=1, escala_id=1, ordem=1))
    for i in (1, 2, 3):
        s.add(Participacao(militar_id=i, escala_id=1))
    s.commit()
    yield s
    s.close()


def _servico(db, dia, militar_id):
    db.add(Servico(escala_id=1, posto_id=1, militar_id=militar_id, dia=dia, cor=Cor.PRETA,
                   inicio_dt=datetime.combine(dia, datetime.min.time()),
                   termino_dt=datetime.combine(dia + timedelta(days=1), datetime.min.time())))
    db.commit()


# --- barra de cobertura -------------------------------------------------------
def test_pct_coberto(db):
    for i in range(2):                      # 2 dos 4 dias da janela fechados
        _servico(db, HOJE + timedelta(days=i), 1)
    c = painel.cobertura(db, HOJE, dias=3)[0]
    assert c.cobertos == 2 and c.total_na_janela == 4
    assert c.pct_coberto == 50


def test_pct_coberto_sem_dia_na_janela_e_cem(db):
    """Escala que não roda em nenhum dia da janela não está 'descoberta'."""
    db.add(Escala(id=2, nome="Museu", tem_preta=False, folga_minima_horas=24))
    db.flush()
    db.add(Posto(escala_id=2, ordem=1))
    db.commit()
    museu = {c.escala.id: c for c in painel.cobertura(db, HOJE, dias=3)}[2]
    assert museu.total_na_janela == 0 and museu.pct_coberto == 100


# --- barras de completude do cadastro ----------------------------------------
def test_campos_do_cadastro(db):
    s = painel.saude_cadastro(db)
    campos = {c.rotulo: c for c in s.campos}
    antig = campos["Número de antiguidade"]
    assert antig.ok == 4 and antig.total == 4 and antig.pct_ok == 100 and antig.completo
    promo = campos["Data de promoção"]
    assert promo.ok == 0 and promo.faltam == 4 and promo.pct_ok == 0
    assert promo.aviso == "faltam 4"


def test_campo_sem_total_nao_divide_por_zero(db):
    """OM sem praça nenhuma: a barra do nº de antiguidade não pode estourar."""
    for m in db.scalars(select(Militar)):
        m.ativo = False
    db.commit()
    antig = {c.rotulo: c for c in painel.saude_cadastro(db).campos}["Número de antiguidade"]
    assert antig.total == 0 and antig.pct_ok == 100


# --- barra de amplitude da distribuição --------------------------------------
def test_amplitude_com_quem_nunca_serviu(db):
    _servico(db, HOJE, 1)
    _servico(db, HOJE + timedelta(days=1), 1)
    _servico(db, HOJE + timedelta(days=2), 2)
    e = painel.equidade(db, HOJE, HOJE + timedelta(days=10))[0]
    # M3 nunca serviu: o piso é 0, não 1 — senão a barra esconderia o pior caso
    assert e.piso == 0 and e.maximo == 2
    assert e.pct_piso == 0 and e.pct_amplitude == 100
    assert e.desequilibrio == 2 and e.vigiar is True
    assert e.pct_serviram == round(2 * 100 / 3)


def test_amplitude_sem_servico_nenhum(db):
    e = painel.equidade(db, HOJE, HOJE)[0]
    assert e.maximo == 0 and e.pct_piso == 0 and e.pct_amplitude == 0
    assert e.vigiar is False


# --- ranking da fila ----------------------------------------------------------
def test_fila_ordena_por_quem_serviu_menos(db):
    _servico(db, HOJE, 1)
    _servico(db, HOJE + timedelta(days=1), 1)
    _servico(db, HOJE + timedelta(days=2), 2)
    fila = painel.fila_por_servicos(db, 1, HOJE, HOJE + timedelta(days=10))
    assert [l.militar.nome_guerra for l in fila] == ["M3", "M2", "M1"]
    assert [l.servicos for l in fila] == [0, 1, 2]
    # quem tem o MENOR número é marcado; os demais não
    assert [l.proximo for l in fila] == [True, False, False]
    # barra proporcional ao máximo, com piso visível para o zero
    assert fila[2].pct == 100 and fila[0].pct == 6


def test_fila_marca_todos_os_empatados_no_menor(db):
    _servico(db, HOJE, 1)
    fila = painel.fila_por_servicos(db, 1, HOJE, HOJE + timedelta(days=10))
    assert sum(1 for l in fila if l.proximo) == 2      # M2 e M3, ambos com zero


def test_fila_so_traz_participante_ativo(db):
    db.add(Participacao(militar_id=4, escala_id=1, ativo=False))
    db.commit()
    fila = painel.fila_por_servicos(db, 1, HOJE, HOJE)
    assert "M4" not in [l.militar.nome_guerra for l in fila]


def test_fila_vazia_sem_participantes(db):
    db.add(Escala(id=3, nome="Vazia", folga_minima_horas=24))
    db.commit()
    assert painel.fila_por_servicos(db, 3, HOJE, HOJE) == []


# --- linha do tempo dos impedimentos ------------------------------------------
def _imp(db, militar_id, ini, fim):
    i = Impedimento(militar_id=militar_id, tipo_impedimento_id=1, inicio=ini, fim=fim)
    db.add(i)
    db.commit()
    return i


def test_linha_do_tempo_posiciona_as_barras(db):
    _imp(db, 1, HOJE, HOJE + timedelta(days=9))         # em curso
    _imp(db, 2, HOJE + timedelta(days=10), HOJE + timedelta(days=19))   # futuro
    imps = db.scalars(select(Impedimento)).all()
    mil = {m.id: m for m in db.scalars(select(Militar))}
    linha = painel.linha_do_tempo(imps, mil, HOJE)

    assert linha.inicio == HOJE and linha.fim == HOJE + timedelta(days=19)
    assert [b.situacao for b in linha.barras] == ["emcurso", "futuro"]
    assert linha.barras[0].left == 0
    assert linha.barras[1].left == round(10 * 100 / 19, 1)
    assert linha.pct_hoje == 0


def test_linha_do_tempo_ignora_o_que_ja_passou(db):
    """Impedimento encerrado não muda escalação nenhuma."""
    _imp(db, 1, HOJE - timedelta(days=20), HOJE - timedelta(days=10))
    imps = db.scalars(select(Impedimento)).all()
    assert painel.linha_do_tempo(imps, {}, HOJE) is None


def test_linha_do_tempo_limita_as_barras(db):
    for i in range(painel.MAX_BARRAS_TEMPO + 4):
        _imp(db, 1, HOJE + timedelta(days=i), HOJE + timedelta(days=i + 1))
    imps = db.scalars(select(Impedimento)).all()
    linha = painel.linha_do_tempo(imps, {}, HOJE)
    assert len(linha.barras) == painel.MAX_BARRAS_TEMPO
    assert linha.ocultos == 4


def test_barra_nao_transborda_a_faixa(db):
    _imp(db, 1, HOJE, HOJE + timedelta(days=5))
    imps = db.scalars(select(Impedimento)).all()
    linha = painel.linha_do_tempo(imps, {}, HOJE)
    for b in linha.barras:
        assert b.left + b.width <= 100.01


def test_linha_do_tempo_tem_quatro_marcas_de_data(db):
    _imp(db, 1, HOJE, HOJE + timedelta(days=30))
    imps = db.scalars(select(Impedimento)).all()
    linha = painel.linha_do_tempo(imps, {}, HOJE)
    assert [pos for _, pos in linha.marcas] == [0.0, 33.3, 66.7, 100.0]
    assert linha.marcas[0][0] == "3 ago"
