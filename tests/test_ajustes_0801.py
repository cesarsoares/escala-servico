"""Os dois primeiros pedidos anotados em 01/08 (notas.txt, bloco novo).

5. Impressão da previsão por PERÍODO, não só mês cheio: o brigada imprime de 15
   em 15 dias, e 15/ago a 15/set atravessa a virada do mês.
3. Na consulta, a permuta mostra o substituto e, logo abaixo, o substituído.

O que não é do pedido mas está coberto aqui, porque sem isso a entrega mente ou
quebra: a coluna do dia ganha o mês quando o período atravessa a virada (senão
"15" aparece duas vezes sem dizer qual é qual), e período recusado explica o
MOTIVO em vez de 500/422 (a página é aberta — regra 13.1 — e a URL é editável).
"""
from datetime import date, datetime, time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.domain.models import Cor
from app.main import MAX_DIAS_IMPRESSAO, app
from app.models.escala import Escala, Participacao, Posto
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.models.servico import Permuta, Servico
from app.seeds import seed_circulos, seed_postos_graduacao


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessao = Session(engine)
    seed_circulos(sessao)
    seed_postos_graduacao(sessao)
    sessao.add(OrganizacaoMilitar(id=1, nome="Quartel General", sigla="QG"))
    sessao.commit()
    yield sessao
    sessao.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _militar(db, nome):
    pg = db.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "Cap"))
    m = Militar(nome_guerra=nome, nome_completo=f"{nome} de Tal",
                posto_graduacao_id=pg, om_id=1)
    db.add(m)
    db.flush()
    return m


def _escala(db, nome="Oficial de Dia"):
    e = Escala(nome=nome, inicio_servico=time(8, 0), duracao_horas=24,
               folga_minima_horas=48)
    e.postos = [Posto(ordem=1, rotulo="Serviço")]
    db.add(e)
    db.flush()
    return e


def _servico(db, escala, militar, dia: date):
    s = Servico(escala_id=escala.id, posto_id=escala.postos[0].id,
                militar_id=militar.id, dia=dia,
                cor=Cor.VERMELHA if dia.weekday() >= 5 else Cor.PRETA,
                inicio_dt=datetime.combine(dia, time(8, 0)),
                termino_dt=datetime.combine(dia, time(8, 0)))
    db.add(s)
    db.flush()
    return s


@pytest.fixture()
def quinzena(db):
    """Serviços em três meses: julho (fora), 15/ago e 15/set (o período pedido)."""
    e = _escala(db)
    silva, costa, roana = (_militar(db, "SILVA"), _militar(db, "COSTA"),
                           _militar(db, "ROANA"))
    db.add_all([Participacao(militar_id=m.id, escala_id=e.id)
                for m in (silva, costa, roana)])
    _servico(db, e, silva, date(2026, 7, 20))
    _servico(db, e, costa, date(2026, 8, 15))
    _servico(db, e, roana, date(2026, 9, 15))
    db.commit()
    return e


# --- 5. impressão por período ------------------------------------------------

def test_periodo_atravessa_a_virada_do_mes(client, quinzena):
    """O caso do pedido: 15/ago a 15/set num documento só."""
    r = client.get(f"/escalas/{quinzena.id}/impressao?inicio=2026-08-15&fim=2026-09-15")
    assert r.status_code == 200
    assert "Cap COSTA (QG)" in r.text and "Cap ROANA (QG)" in r.text
    assert "15/08/2026 a 15/09/2026" in r.text


def test_periodo_nao_traz_dia_de_fora(client, quinzena):
    texto = client.get(
        f"/escalas/{quinzena.id}/impressao?inicio=2026-08-15&fim=2026-09-15").text
    assert "Cap SILVA (QG)" not in texto           # 20/jul


def test_pontas_do_periodo_entram(client, db):
    """Intervalo fechado nos dois lados: o primeiro e o último dia contam."""
    e = _escala(db)
    a, b = _militar(db, "ALFA"), _militar(db, "BRAVO")
    _servico(db, e, a, date(2026, 8, 15))
    _servico(db, e, b, date(2026, 9, 15))
    db.commit()
    texto = client.get(f"/escalas/{e.id}/impressao?inicio=2026-08-15&fim=2026-09-15").text
    assert "Cap ALFA (QG)" in texto and "Cap BRAVO (QG)" in texto


def test_dia_ganha_o_mes_quando_o_periodo_atravessa(client, quinzena):
    """Sem isso o documento traz '15' duas vezes, sem dizer qual é qual."""
    texto = client.get(
        f"/escalas/{quinzena.id}/impressao?inicio=2026-08-15&fim=2026-09-15").text
    assert "15/08" in texto and "15/09" in texto


def test_mes_cheio_nao_repete_o_mes_na_coluna_do_dia(client, quinzena):
    """O caso comum continua enxuto: dentro de um mês só, o dia basta."""
    texto = client.get(f"/escalas/{quinzena.id}/impressao?ano=2026&mes=8").text
    assert "agosto de 2026" in texto.lower()
    assert "15/08" not in texto


def test_periodo_tem_precedencia_sobre_ano_e_mes(client, quinzena):
    """Com os dois na URL, vale o período — é o mais específico."""
    texto = client.get(
        f"/escalas/{quinzena.id}/impressao?ano=2026&mes=7"
        "&inicio=2026-08-15&fim=2026-09-15").text
    assert "15/08/2026 a 15/09/2026" in texto
    assert "Cap SILVA (QG)" not in texto           # o serviço de julho


def test_data_final_antes_da_inicial_diz_o_motivo(client, quinzena):
    """Pedido 4 do mesmo bloco: restrição sem motivo vira 'não funciona'."""
    r = client.get(f"/escalas/{quinzena.id}/impressao?inicio=2026-09-15&fim=2026-08-15")
    assert r.status_code == 200
    assert "anterior à inicial" in r.text


def test_periodo_longo_demais_e_recusado_com_motivo(client, quinzena):
    """Página aberta: sem teto, um pedido monta uma tabela de anos."""
    r = client.get(
        f"/escalas/{quinzena.id}/impressao?ano=2026&mes=8&inicio=2026-01-01&fim=2030-12-31")
    assert r.status_code == 200
    assert str(MAX_DIAS_IMPRESSAO) in r.text
    # não renderizou os cinco anos pedidos: caiu no mês
    assert "agosto de 2026" in r.text.lower()
    assert "Cap ROANA (QG)" not in r.text          # 15/set, fora do mês


def test_data_ilegivel_nao_estoura(client, quinzena):
    """A URL é editável à mão; 'ontem' não pode virar 500 nem 422 em branco."""
    r = client.get(f"/escalas/{quinzena.id}/impressao?inicio=ontem&fim=2026-09-15")
    assert r.status_code == 200
    assert "Informe as duas datas" in r.text


def test_uma_data_so_e_recusada(client, quinzena):
    r = client.get(f"/escalas/{quinzena.id}/impressao?inicio=2026-08-15")
    assert r.status_code == 200
    assert "Informe as duas datas" in r.text


def test_periodo_recusado_cai_no_mes_em_vez_de_pagina_vazia(client, quinzena):
    """Recusar não pode deixar o gestor sem documento nenhum."""
    texto = client.get(
        f"/escalas/{quinzena.id}/impressao?ano=2026&mes=8&inicio=2026-09-15&fim=2026-08-15").text
    assert "agosto de 2026" in texto.lower()
    assert "Cap COSTA (QG)" in texto


def test_periodo_recusado_devolve_o_que_foi_digitado(client, quinzena):
    """Convenção da casa: erro não pode limpar o formulário — corrigir uma data
    não pode obrigar a redigitar as duas."""
    texto = client.get(
        f"/escalas/{quinzena.id}/impressao?inicio=2026-09-15&fim=2026-08-15").text
    assert 'name="inicio" value="2026-09-15"' in texto
    assert 'name="fim" value="2026-08-15"' in texto


def test_ano_fora_da_faixa_e_recusado(client, quinzena):
    """Mesma faixa de ?ano= (ANO_MIN..ANO_MAX): date(0,…) estoura."""
    r = client.get(f"/escalas/{quinzena.id}/impressao?inicio=0001-01-01&fim=0001-01-31")
    assert r.status_code == 200
    assert "Informe as duas datas" in r.text


def test_formulario_do_periodo_vem_preenchido_com_o_periodo_em_vista(client, quinzena):
    texto = client.get(f"/escalas/{quinzena.id}/impressao?ano=2026&mes=8").text
    assert 'name="inicio" value="2026-08-01"' in texto
    assert 'name="fim" value="2026-08-31"' in texto


def test_periodo_segue_aberto_sem_login(client, quinzena):
    """Regra 13.1: o documento publicado não exige sessão, com ou sem período."""
    r = client.get(f"/escalas/{quinzena.id}/impressao?inicio=2026-08-15&fim=2026-09-15")
    assert r.status_code == 200


def test_periodo_sem_servico_nao_quebra(client, quinzena):
    r = client.get(f"/escalas/{quinzena.id}/impressao?inicio=2026-11-01&fim=2026-11-30")
    assert r.status_code == 200
    assert "Nenhum serviço escalado" in r.text


# --- 1. a folga mínima saiu da permuta (regra 10.5, reescrita em 01/08) ------
# O grosso está em test_permuta_service.py e test_permutas_web.py. O que fica
# aqui é a fronteira: o afrouxamento vale SÓ para a permuta.

def test_a_folga_continua_barrando_a_substituicao_por_conflito(db):
    """Conflitos ≠ permuta. Na permuta o escalado não muda e a folga fica com
    ele; na substituição por conflito o candidato VIRA o escalado — ganha a
    folga e entra na fila por este serviço. Por isso a guarda continua (7.4)."""
    from app.services import conflitos

    e = _escala(db)
    silva, roana = _militar(db, "SILVA"), _militar(db, "ROANA")
    db.add_all([Participacao(militar_id=silva.id, escala_id=e.id),
                Participacao(militar_id=roana.id, escala_id=e.id)])
    _servico(db, e, roana, date(2026, 8, 3))            # ROANA saiu na véspera
    alvo = _servico(db, e, silva, date(2026, 8, 4))
    db.commit()

    with pytest.raises(conflitos.SubstituicaoNegada) as excinfo:
        conflitos.substituir(db, alvo.id, roana.id)
    assert "folga mínima" in str(excinfo.value)


# --- 3. permuta na consulta --------------------------------------------------

def test_consulta_mostra_substituto_acima_do_substituido(client, db):
    """Pedido 3: quem assume o serviço primeiro, o escalado logo abaixo."""
    e = _escala(db)
    silva, roana = _militar(db, "SILVA"), _militar(db, "ROANA")
    s = _servico(db, e, silva, date(2026, 8, 3))
    db.add(Permuta(servico_id=s.id, militar_substituto_id=roana.id))
    db.commit()
    texto = client.get(f"/?escala_id={e.id}&ano=2026&mes=8").text
    assert "ROANA" in texto and "SILVA" in texto
    assert "no lugar de" in texto
    assert texto.index("ROANA") < texto.index("SILVA")


def test_consulta_sem_permuta_mostra_um_nome_so(client, db):
    """Regra 9 vale nos dois sentidos: sem permuta, nada de segunda linha."""
    e = _escala(db)
    silva = _militar(db, "SILVA")
    _servico(db, e, silva, date(2026, 8, 3))
    db.commit()
    texto = client.get(f"/?escala_id={e.id}&ano=2026&mes=8").text
    assert "SILVA" in texto
    assert "no lugar de" not in texto


def test_permuta_de_outra_escala_nao_vaza_para_a_consulta(client, db):
    """A consulta é de UMA escala: a permuta carregada tem de ser dos serviços dela."""
    a, b = _escala(db, "Oficial de Dia"), _escala(db, "Museu")
    silva, roana = _militar(db, "SILVA"), _militar(db, "ROANA")
    s = _servico(db, b, silva, date(2026, 8, 3))
    db.add(Permuta(servico_id=s.id, militar_substituto_id=roana.id))
    db.commit()
    texto = client.get(f"/?escala_id={a.id}&ano=2026&mes=8").text
    assert "no lugar de" not in texto
    assert "ROANA" not in texto
