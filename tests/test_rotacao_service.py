"""Testes da camada de serviço de rotação (app/services/rotacao.py).

Diferente dos testes do domínio (puros), estes exercitam a ponte ORM<->domínio
num SQLite em memória: seed mínimo, cria escala/militares e roda o motor gravando
`servico`, verificando rotação cronológica, folga entre concorrentes e o aviso de
efetivo insuficiente (regras 6/7).
"""
from datetime import date, time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models.escala import Escala, EscalaConcorrente, Participacao, Posto
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.seeds import seed_circulos, seed_postos_graduacao
from app.services import rotacao


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


def _sgt_id(db) -> int:
    return db.scalar(
        __import__("sqlalchemy").select(PostoGraduacao.id).where(PostoGraduacao.sigla == "2º Sgt")
    )


def _militar(db, id_: int, antig: int) -> Militar:
    """Cria um 2º Sgt; numero_antiguidade cresce -> mais moderno (desempata a fila)."""
    m = Militar(
        id=id_, nome_guerra=f"M{id_}", nome_completo=f"Militar {id_}",
        identidade=f"ID{id_}", cpf=f"CPF{id_}",
        posto_graduacao_id=_sgt_id(db), om_id=1,
        data_promocao=date(2020, 1, 1), data_praca=date(2010, 1, 1),
        numero_antiguidade=antig,
    )
    db.add(m)
    return m


def _escala(db, id_: int, nome: str, postos: int = 1, folga: int | None = None,
            inicio: time = time(8, 0), duracao: int = 24) -> Escala:
    e = Escala(id=id_, nome=nome, folga_minima_horas=folga,
               inicio_servico=inicio, duracao_horas=duracao)
    db.add(e)
    db.flush()
    for ordem in range(1, postos + 1):
        db.add(Posto(escala_id=id_, ordem=ordem, rotulo=f"Posto {ordem}"))
    db.flush()
    return e


def _participa(db, escala_id: int, *militar_ids: int):
    for mid in militar_ids:
        db.add(Participacao(militar_id=mid, escala_id=escala_id, ativo=True))
    db.flush()


def test_rotacao_ciclo_completo_em_dias_uteis(db):
    # 3 sargentos, 1 posto, folga 48h. Sem histórico, a fila desempata por
    # antiguidade: mais moderno (maior nº) primeiro (regra 9.5).
    for i, antig in [(1, 30), (2, 20), (3, 10)]:
        _militar(db, i, antig)
    _escala(db, 1, "Oficial de Dia", postos=1, folga=48)
    _participa(db, 1, 1, 2, 3)

    # seg-ter-qua-qui-sex-seg: com folga 48h e 3 militares, cada um serve e folga
    # dois dias. Ordem esperada: M1, M2, M3, M1, M2 (dia útil = preta).
    res = rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 7, 20), date(2026, 7, 24))
    servidos = [r.escolhidos[0].id for r in res]
    assert servidos == [1, 2, 3, 1, 2]
    assert all(not r.efetivo_insuficiente for r in res)


def test_efetivo_insuficiente_para_o_piso(db):
    # 1 militar só, folga 48h: serve dia 1, mas não pode reassumir no dia 2
    # (nem no 3, saiu às 08h do dia 3). Vira efetivo insuficiente (regra 7.8).
    _militar(db, 1, 10)
    _escala(db, 1, "Solo", postos=1, folga=48)
    _participa(db, 1, 1)

    res = rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 7, 20), date(2026, 7, 22))
    assert res[0].escolhidos and not res[0].efetivo_insuficiente
    assert res[1].efetivo_insuficiente and res[1].escolhidos == []  # dia seguinte: sem folga
    assert res[2].efetivo_insuficiente and res[2].escolhidos == []  # ainda dentro das 48h


def test_folga_minima_atravessa_escalas_concorrentes(db):
    # M1 serve na escala A no dia 20 (term. 21 08:00). A e B são concorrentes,
    # B com folga 48h: M1 não pode assumir B no dia 21 (regra 7.4).
    _militar(db, 1, 30)
    _militar(db, 2, 20)
    a = _escala(db, 1, "Escala A", postos=1, folga=48)
    b = _escala(db, 2, "Escala B", postos=1, folga=48)
    # relação simétrica, menor<maior (regra 7.4.1)
    db.add(EscalaConcorrente(escala_menor_id=1, escala_maior_id=2))
    _participa(db, 1, 1)          # só M1 na A
    _participa(db, 2, 1, 2)       # M1 e M2 na B
    db.flush()

    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 7, 20), date(2026, 7, 20))  # M1 serve A dia 20
    r = rotacao.escalar_dia(db, 2, date(2026, 7, 21))
    # M1 está sem folga (serviu A ontem); assume M2, o outro participante de B
    assert r.escolhidos[0].id == 2


def test_museu_so_roda_vermelha(db):
    # escala só-vermelha (tem_preta=False): num dia útil não gera serviço (regra 4.5)
    _militar(db, 1, 10)
    e = Escala(id=1, nome="Museu", tem_preta=False, tem_vermelha=True)
    db.add(e)
    db.flush()
    db.add(Posto(escala_id=1, ordem=1))
    _participa(db, 1, 1)
    db.flush()

    util = rotacao.escalar_dia(db, 1, date(2026, 7, 20))     # segunda -> preta
    fds = rotacao.escalar_dia(db, 1, date(2026, 7, 25))      # sábado -> vermelha
    assert util.escolhidos == []
    assert fds.escolhidos and fds.escolhidos[0].id == 1
