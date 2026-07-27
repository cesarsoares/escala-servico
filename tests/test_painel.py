"""Blocos do painel do gestor (`services/painel`).

Cada bloco responde a uma pergunta que muda a decisão do gestor: até quando a
escala está fechada, o que exige ação, quem serve hoje, se o cadastro sustenta
os desempates (regra 9) e se a distribuição está equilibrada.
"""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.domain.models import Cor
from app.models.calendario import Feriado
from app.models.escala import Escala, Participacao, Posto
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import (
    OrganizacaoMilitar, PostoGraduacao, TipoImpedimento,
)
from app.models.servico import Permuta, Servico
from app.seeds import seed_circulos, seed_postos_graduacao, seed_tipos_impedimento
from app.services import painel

HOJE = date(2026, 8, 3)      # segunda-feira


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = Session(engine)
    seed_circulos(s)
    seed_postos_graduacao(s)
    seed_tipos_impedimento(s)
    s.add(OrganizacaoMilitar(id=1, nome="Quartel General", sigla="QG"))
    s.flush()
    sgt = s.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "2º Sgt"))
    for i in (1, 2, 3):
        s.add(Militar(id=i, nome_guerra=f"M{i}", nome_completo=f"Militar {i}",
                      posto_graduacao_id=sgt, om_id=1, numero_antiguidade=100 - i * 10))
    # escala 1 roda as duas cores; escala 2 é só-vermelha (o caso do museu)
    s.add(Escala(id=1, nome="Oficial de Dia", folga_minima_horas=24))
    s.add(Escala(id=2, nome="Museu", tem_preta=False, folga_minima_horas=24))
    s.flush()
    s.add(Posto(id=1, escala_id=1, ordem=1))
    s.add(Posto(id=2, escala_id=2, ordem=1))
    for m in (1, 2, 3):
        s.add(Participacao(militar_id=m, escala_id=1))
    s.add(Participacao(militar_id=1, escala_id=2))
    s.commit()
    yield s
    s.close()


def _servico(db, escala_id, dia, militar_id, posto_id=1, cor=Cor.PRETA):
    db.add(Servico(escala_id=escala_id, posto_id=posto_id, militar_id=militar_id,
                   dia=dia, cor=cor,
                   inicio_dt=datetime.combine(dia, datetime.min.time()).replace(hour=8),
                   termino_dt=datetime.combine(dia + timedelta(days=1),
                                               datetime.min.time()).replace(hour=8)))
    db.commit()


# --- 1. Cobertura -------------------------------------------------------------
def test_cobertura_acusa_dia_sem_ninguem_escalado(db):
    _servico(db, 1, HOJE, 1)          # só hoje está fechado
    c = {x.escala.id: x for x in painel.cobertura(db, HOJE, dias=3)}[1]
    assert c.fechada_ate == HOJE
    assert c.em_dia is False
    assert c.primeiro_descoberto == HOJE + timedelta(days=1)
    assert len(c.descobertos) == 3    # amanhã, depois e o terceiro dia


def test_cobertura_em_dia_quando_tudo_fechado(db):
    for i in range(4):
        _servico(db, 1, HOJE + timedelta(days=i), 1)
    c = {x.escala.id: x for x in painel.cobertura(db, HOJE, dias=3)}[1]
    assert c.em_dia is True and c.descobertos == []


def test_cobertura_ignora_dia_que_a_escala_nao_roda(db):
    """A escala só-vermelha não pode acusar buraco em dia útil (regra 4.5)."""
    # 03/08/2026 é segunda; a janela de 4 dias vai até quinta — nenhum dia vermelho
    museu = {x.escala.id: x for x in painel.cobertura(db, HOJE, dias=3)}[2]
    assert museu.total_na_janela == 0 and museu.descobertos == []


def test_cobertura_conta_o_fim_de_semana_para_a_so_vermelha(db):
    # janela de 7 dias a partir de segunda alcança sábado e domingo
    museu = {x.escala.id: x for x in painel.cobertura(db, HOJE, dias=7)}[2]
    assert museu.total_na_janela == 2          # sáb 08 e dom 09
    assert len(museu.descobertos) == 2


def test_cobertura_considera_feriado_como_vermelha(db):
    db.add(Feriado(data=HOJE + timedelta(days=1), nome="Feriado de teste"))
    db.commit()
    museu = {x.escala.id: x for x in painel.cobertura(db, HOJE, dias=3)}[2]
    assert museu.total_na_janela == 1          # o feriado, que é dia vermelho


# --- 2. Alertas ---------------------------------------------------------------
def test_alerta_de_escalado_impedido(db):
    """O caso real: a dispensa é lançada DEPOIS de o mês estar fechado."""
    _servico(db, 1, HOJE + timedelta(days=2), 1)
    db.add(Impedimento(militar_id=1, tipo_impedimento_id=1,
                       inicio=HOJE + timedelta(days=1), fim=HOJE + timedelta(days=5)))
    db.commit()
    a = painel.alertas(db, HOJE)
    assert len(a.conflitos) == 1
    assert a.conflitos[0]["militar"].endswith("M1")
    assert a.conflitos[0]["dia"] == HOJE + timedelta(days=2)


def test_conflito_so_olha_para_frente(db):
    """Serviço passado com impedimento é história, não pendência."""
    _servico(db, 1, HOJE - timedelta(days=5), 1)
    db.add(Impedimento(militar_id=1, tipo_impedimento_id=1,
                       inicio=HOJE - timedelta(days=6), fim=HOJE - timedelta(days=1)))
    db.commit()
    assert painel.alertas(db, HOJE).conflitos == []


def test_alerta_de_efetivo_curto(db):
    """Dia gravado com menos militares do que vagas (regra 7.8)."""
    db.add(Posto(id=3, escala_id=1, ordem=2))     # escala passa a ter 2 vagas
    db.commit()
    _servico(db, 1, HOJE, 1)                      # só uma preenchida
    a = painel.alertas(db, HOJE)
    assert len(a.efetivo_curto) == 1
    assert a.efetivo_curto[0]["gravados"] == 1 and a.efetivo_curto[0]["postos"] == 2


def test_alerta_de_escala_com_menos_gente_que_vagas(db):
    for ordem in (2, 3, 4, 5):
        db.add(Posto(escala_id=1, ordem=ordem))   # 5 vagas para 3 participantes
    db.commit()
    a = painel.alertas(db, HOJE)
    assert len(a.mal_configuradas) == 1
    assert a.mal_configuradas[0]["participantes"] == 3
    assert a.mal_configuradas[0]["postos"] == 5


def test_sem_alertas_quando_esta_tudo_certo(db):
    _servico(db, 1, HOJE, 1)
    assert painel.alertas(db, HOJE).total == 0


# --- 3. Serviço do dia e fila -------------------------------------------------
def test_servico_do_dia(db):
    _servico(db, 1, HOJE, 2)
    linhas = painel.servico_do_dia(db, HOJE)
    assert len(linhas) == 1
    assert linhas[0]["militar"].endswith("M2") and linhas[0]["escala"] == "Oficial de Dia"
    assert linhas[0]["substituto"] is None


def test_servico_do_dia_mostra_os_dois_na_permuta(db):
    """A folga é de quem estava escalado, nunca de quem cobriu (regra 9)."""
    _servico(db, 1, HOJE, 2)
    sid = db.scalar(select(Servico.id))
    db.add(Permuta(servico_id=sid, militar_substituto_id=3))
    db.commit()
    linha = painel.servico_do_dia(db, HOJE)[0]
    assert linha["militar"].endswith("M2")        # o escalado continua aparecendo
    assert linha["substituto"].endswith("M3")


def test_proximos_da_fila_nao_grava_nada(db):
    antes = db.scalar(select(Servico.id))
    proximos = painel.proximos_da_fila(db, HOJE)
    assert db.scalar(select(Servico.id)) == antes      # nada foi gravado
    fila = {p["escala"]: p for p in proximos}
    assert "Oficial de Dia" in fila
    assert fila["Oficial de Dia"]["militares"]         # o motor indicou alguém
    assert "Museu" not in fila                          # segunda-feira: só-vermelha não roda


# --- 4. Saúde do cadastro -----------------------------------------------------
def test_saude_do_cadastro_conta_o_que_falta(db):
    s = painel.saude_cadastro(db)
    assert s.ativos == 3
    assert s.sem_promocao == 3 and s.sem_nascimento == 3 and s.sem_identidade == 3
    assert s.pracas == 3 and s.pracas_sem_antiguidade == 0    # os 3 têm o número
    assert s.completo is False


def test_saude_completa_quando_os_desempates_estao_preenchidos(db):
    for m in db.scalars(select(Militar)):
        m.data_promocao = date(2020, 1, 1)
        m.data_nascimento = date(1990, 1, 1)
    db.commit()
    assert painel.saude_cadastro(db).completo is True


def test_saude_acusa_praca_sem_numero_de_antiguidade(db):
    """Regra 9.5: sem o número, o desempate da graduação não tem critério."""
    db.get(Militar, 2).numero_antiguidade = None
    db.commit()
    assert painel.saude_cadastro(db).pracas_sem_antiguidade == 1


def test_saude_acusa_militar_fora_de_qualquer_escala(db):
    db.add(Militar(id=9, nome_guerra="M9", nome_completo="Militar 9",
                   posto_graduacao_id=db.scalar(select(PostoGraduacao.id)), om_id=1))
    db.commit()
    assert painel.saude_cadastro(db).sem_escala == 1


# --- 5. Equidade --------------------------------------------------------------
def test_equidade_mede_a_distribuicao(db):
    _servico(db, 1, HOJE, 1)
    _servico(db, 1, HOJE + timedelta(days=1), 1)
    _servico(db, 1, HOJE + timedelta(days=2), 2)
    e = {x.escala_id: x for x in painel.equidade(db, HOJE, HOJE + timedelta(days=10))}[1]
    assert e.participantes == 3 and e.servidos == 2
    assert e.minimo == 1 and e.maximo == 2
    assert e.nunca_serviram == 1
    # quem nunca serviu conta como zero: a diferença real é 2, não 1
    assert e.desequilibrio == 2


def test_equidade_sem_servico_no_periodo(db):
    e = {x.escala_id: x for x in painel.equidade(db, HOJE, HOJE)}[1]
    assert e.servidos == 0 and e.maximo == 0 and e.desequilibrio == 0


# --- 6. Feriados próximos -----------------------------------------------------
def test_feriados_da_janela(db):
    db.add(Feriado(data=HOJE + timedelta(days=5), nome="Dentro da janela"))
    db.add(Feriado(data=HOJE + timedelta(days=90), nome="Fora da janela"))
    db.commit()
    nomes = [f["nome"] for f in painel.dias_vermelhos_proximos(db, HOJE, dias=30)]
    assert nomes == ["Dentro da janela"]
