"""Testes do serviço de permuta e da concorrência (app/services).

Permuta = registro puro. A folga NÃO muda de dono (regra 9) e, desde 01/08/2026,
também NÃO barra a troca (regra 10.5 reescrita pelo Brigada): cobrir não conta na
folga de quem cobre. Nega-se só o impossível — impedido no dia, já de serviço no
dia. Reaproveita o motor para escalar o histórico.
"""
from datetime import date, time

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models.escala import Escala, Participacao, Posto
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao, TipoImpedimento
from app.models.servico import Servico
from app.seeds import seed_circulos, seed_postos_graduacao
from app.services import escala_service, rotacao
from app.services.permuta import PermutaNegada, cancelar_permuta, registrar_permuta


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed_circulos(s)
        seed_postos_graduacao(s)
        s.add(OrganizacaoMilitar(id=1, nome="Quartel General", sigla="QG"))
        s.flush()
        yield s


def _sgt(db):
    return db.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "2º Sgt"))


def _militar(db, id_):
    # nº de antiguidade DECRESCE com o id: maior nº = mais moderno = 1º da fila
    # (regra 9.5). Assim M1 serve antes de M2, deixando os testes previsíveis.
    db.add(Militar(
        id=id_, nome_guerra=f"M{id_}", nome_completo=f"Militar {id_}",
        identidade=f"ID{id_}", cpf=f"CPF{id_}", posto_graduacao_id=_sgt(db), om_id=1,
        data_promocao=date(2020, 1, 1), data_praca=date(2010, 1, 1),
        numero_antiguidade=100 - id_ * 10,
    ))


def _escala(db, id_, nome, folga=48):
    db.add(Escala(id=id_, nome=nome, folga_minima_horas=folga))
    db.flush()
    db.add(Posto(escala_id=id_, ordem=1, rotulo="Posto 1"))
    db.flush()


def _participa(db, escala_id, *ids):
    for mid in ids:
        db.add(Participacao(militar_id=mid, escala_id=escala_id, ativo=True))
    db.flush()


def _servico_do_dia(db, escala_id, dia):
    return db.scalar(select(Servico).where(Servico.escala_id == escala_id, Servico.dia == dia))


def test_permuta_ok_registra_sem_mover_folga(db):
    # M1 escalado no dia 20; M2 está folgado (nunca serviu) -> pode cobrir.
    _militar(db, 1); _militar(db, 2)
    _escala(db, 1, "Oficial de Dia")
    _participa(db, 1, 1, 2)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 7, 20), date(2026, 7, 20))
    servico = _servico_do_dia(db, 1, date(2026, 7, 20))
    assert servico.militar_id == 1

    p = registrar_permuta(db, servico.id, militar_substituto_id=2, observacao="troca")
    assert p.militar_substituto_id == 2
    # a folga NÃO muda de dono: o serviço segue no escalado (regra 9)
    db.refresh(servico)
    assert servico.militar_id == 1


def test_folga_minima_nao_barra_a_permuta(db):
    """Regra 10.5 reescrita em 01/08/2026 — o caso que o Brigada relatou.

    M1 e M2 servem em dias consecutivos. M1 quer cobrir o serviço de M2 no dia
    seguinte ao seu: ANTES era recusado por "24h < 48h". Agora é aceito —
    cobrir não conta na folga de quem cobre, a contagem fica com o substituído
    (10.2), então não há folga a ferir. Quem julga o descanso é quem autoriza.
    """
    _militar(db, 1); _militar(db, 2)
    _escala(db, 1, "Solo", folga=48)
    _participa(db, 1, 1, 2)
    res = rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 7, 20), date(2026, 7, 21))
    assert res[0].escolhidos[0].id == 1   # M1 serve dia 20
    assert res[1].escolhidos[0].id == 2   # M2 serve dia 21 (M1 sem folga)

    servico_21 = _servico_do_dia(db, 1, date(2026, 7, 21))  # de M2
    p = registrar_permuta(db, servico_21.id, militar_substituto_id=1)  # M1 saiu ontem
    assert p.militar_substituto_id == 1
    # e a folga do dia 21 continua sendo de M2, que estava escalado (regra 9)
    db.refresh(servico_21)
    assert servico_21.militar_id == 2


def test_a_escalacao_automatica_continua_respeitando_a_folga(db):
    """A 10.5 mudou só para a TROCA: o motor (7.4) não afrouxou junto.

    Sem esta guarda, afrouxar a permuta poderia ser lido como afrouxar a folga —
    e aí a escala automática passaria a pôr o mesmo militar em dias seguidos.
    """
    _militar(db, 1); _militar(db, 2)
    _escala(db, 1, "Solo", folga=48)
    _participa(db, 1, 1, 2)
    res = rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 7, 20), date(2026, 7, 21))
    assert res[0].escolhidos[0].id == 1
    assert res[1].escolhidos[0].id == 2   # M1 não repete no dia seguinte


def test_permuta_negada_substituto_impedido(db):
    _militar(db, 1); _militar(db, 2)
    _escala(db, 1, "Oficial de Dia")
    _participa(db, 1, 1, 2)
    db.add(TipoImpedimento(id=1, nome="Férias"))
    db.flush()
    db.add(Impedimento(militar_id=2, tipo_impedimento_id=1,
                       inicio=date(2026, 7, 20), fim=date(2026, 7, 25)))
    db.flush()
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 7, 20), date(2026, 7, 20))
    servico = _servico_do_dia(db, 1, date(2026, 7, 20))  # M1

    with pytest.raises(PermutaNegada):
        registrar_permuta(db, servico.id, militar_substituto_id=2)  # M2 de férias


def test_permuta_negada_substituto_e_o_proprio_e_servico_ja_permutado(db):
    _militar(db, 1); _militar(db, 2)
    _escala(db, 1, "Oficial de Dia")
    _participa(db, 1, 1, 2)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 7, 20), date(2026, 7, 20))
    servico = _servico_do_dia(db, 1, date(2026, 7, 20))  # M1

    with pytest.raises(PermutaNegada):
        registrar_permuta(db, servico.id, militar_substituto_id=1)  # é o próprio

    registrar_permuta(db, servico.id, militar_substituto_id=2)
    with pytest.raises(PermutaNegada):
        registrar_permuta(db, servico.id, militar_substituto_id=2)  # já permutado

    assert cancelar_permuta(db, servico.id) is True
    assert cancelar_permuta(db, servico.id) is False  # idempotente


def test_permuta_negada_substituto_ja_de_servico_no_dia(db):
    # M1 serve na escala A e M2 na escala B no mesmo dia; A e B concorrentes.
    # M2 não pode cobrir o serviço de M1 (estaria em dois serviços simultâneos).
    _militar(db, 1); _militar(db, 2)
    _escala(db, 1, "Escala A")
    _escala(db, 2, "Escala B")
    escala_service.declarar_concorrencia(db, 1, 2)
    _participa(db, 1, 1)   # M1 na A
    _participa(db, 2, 2)   # M2 na B
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 7, 20), date(2026, 7, 20))  # M1 serve A
    rotacao.escalar_e_gravar_periodo(db, 2, date(2026, 7, 20), date(2026, 7, 20))  # M2 serve B
    servico_a = _servico_do_dia(db, 1, date(2026, 7, 20))  # de M1

    with pytest.raises(PermutaNegada):
        registrar_permuta(db, servico_a.id, militar_substituto_id=2)  # M2 já serve hoje


def test_declarar_concorrencia_normaliza_e_e_idempotente(db):
    _escala(db, 5, "Escala 5")
    _escala(db, 2, "Escala 2")
    # ordem invertida na entrada: deve gravar menor<maior (2,5)
    par = escala_service.declarar_concorrencia(db, 5, 2)
    assert (par.escala_menor_id, par.escala_maior_id) == (2, 5)
    # idempotente: declarar de novo (em qualquer ordem) não duplica
    escala_service.declarar_concorrencia(db, 2, 5)
    total = db.scalar(select(sa.func.count()).select_from(
        __import__("app.models.escala", fromlist=["EscalaConcorrente"]).EscalaConcorrente))
    assert total == 1
    assert escala_service.concorrentes_de(db, 2) == [5]
    assert escala_service.concorrentes_de(db, 5) == [2]
    assert escala_service.remover_concorrencia(db, 5, 2) is True
    assert escala_service.remover_concorrencia(db, 5, 2) is False
