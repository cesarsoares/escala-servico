"""Conflito serviço × impedimento lançado depois (demanda do Brigada, 30/07).

Parte um: o motor NÃO erra — impedimento lançado antes de escalar já pulava o
militar, e os primeiros testes provam isso ponta a ponta, porque foi essa a
queixa. O que faltava era desfazer o que já estava gravado quando a dispensa
chega depois do mês fechado, SEM refazer o mês inteiro (regra 7.5 + 9).
"""
from datetime import date, time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.domain.models import Cor
from app.models.escala import Escala, EscalaConcorrente, Participacao, Posto
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao, TipoImpedimento
from app.models.servico import Permuta, Servico
from app.seeds import seed_circulos, seed_postos_graduacao, seed_tipos_impedimento
from app.services import conflitos, rotacao


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed_circulos(s)
        seed_postos_graduacao(s)
        seed_tipos_impedimento(s)
        s.add(OrganizacaoMilitar(id=1, nome="Quartel General", sigla="QG"))
        s.flush()
        yield s


def _sgt(db) -> int:
    return db.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "2º Sgt"))


def _militar(db, id_: int, antig: int) -> Militar:
    """2º Sgt; maior `numero_antiguidade` = mais moderno = topo do desempate."""
    m = Militar(
        id=id_, nome_guerra=f"M{id_}", nome_completo=f"Militar {id_}",
        identidade=f"ID{id_}", cpf=f"CPF{id_}", posto_graduacao_id=_sgt(db), om_id=1,
        data_promocao=date(2020, 1, 1), data_praca=date(2010, 1, 1),
        numero_antiguidade=antig,
    )
    db.add(m)
    db.flush()      # sem relação declarada, o flush da participação não ordena sozinho
    return m


def _escala(db, id_: int, nome: str, postos: int = 1, folga: int | None = 48,
            inicio: time = time(8, 0), duracao: int = 24) -> Escala:
    e = Escala(id=id_, nome=nome, folga_minima_horas=folga,
               inicio_servico=inicio, duracao_horas=duracao)
    db.add(e)
    db.flush()
    for ordem in range(1, postos + 1):
        db.add(Posto(escala_id=id_, ordem=ordem, rotulo=f"Posto {ordem}"))
    db.flush()
    return e


def _participa(db, escala_id: int, *militar_ids: int, **kw):
    for mid in militar_ids:
        db.add(Participacao(militar_id=mid, escala_id=escala_id, ativo=True, **kw))
    db.flush()


def _impede(db, militar_id: int, inicio: date, fim: date) -> Impedimento:
    tid = db.scalar(select(TipoImpedimento.id).order_by(TipoImpedimento.id))
    imp = Impedimento(militar_id=militar_id, tipo_impedimento_id=tid,
                      inicio=inicio, fim=fim, observacao="dispensa")
    db.add(imp)
    db.flush()
    return imp


def _escalados(db) -> dict[date, int]:
    return {s.dia: s.militar_id for s in db.scalars(select(Servico).order_by(Servico.dia))}


def _cenario(db, folga: int = 48, postos: int = 1):
    """3 sargentos, 1 escala. Base de quase todos os testes."""
    for i, antig in [(1, 30), (2, 20), (3, 10)]:
        _militar(db, i, antig)
    _escala(db, 1, "Oficial de Dia", postos=postos, folga=folga)
    _participa(db, 1, 1, 2, 3)


# --- o motor não erra: impedimento ANTES de escalar (a queixa relatada) ------

def test_impedido_antes_de_escalar_nao_entra(db):
    _cenario(db)
    _impede(db, 1, date(2026, 8, 3), date(2026, 8, 14))
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 14))
    assert 1 not in _escalados(db).values()


def test_impedimento_parcial_pula_so_o_periodo(db):
    _cenario(db)
    _impede(db, 1, date(2026, 8, 3), date(2026, 8, 7))
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 14))
    esc = _escalados(db)
    assert all(m != 1 for d, m in esc.items() if d <= date(2026, 8, 7))
    assert 1 in [m for d, m in esc.items() if d > date(2026, 8, 7)]


def test_impedimento_de_um_dia_so(db):
    """Borda: inicio == fim. `cobre()` é inclusivo nas duas pontas."""
    _cenario(db)
    _impede(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 14))
    assert _escalados(db)[date(2026, 8, 3)] != 1


def test_reescalar_sem_regravar_nao_desfaz_o_que_ja_estava(db):
    """O que produz a queixa: gravar_dia é idempotente, o mês fechado não muda."""
    _cenario(db)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 14))
    antes = _escalados(db)
    _impede(db, 1, date(2026, 8, 3), date(2026, 8, 14))
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 14))
    assert _escalados(db) == antes
    assert 1 in antes.values()      # o impedido continua lá — e é isso que `conflitos` resolve


# --- detecção do conflito ----------------------------------------------------

def test_conflitos_lista_os_dias_atingidos(db):
    _cenario(db)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 14))
    dias_de_m1 = [d for d, m in _escalados(db).items() if m == 1]
    _impede(db, 1, min(dias_de_m1), max(dias_de_m1))

    achados = conflitos.conflitos(db, militar_id=1)
    assert [c.dia for c in achados] == dias_de_m1
    assert {c.militar for c in achados} == {"2º Sgt M1"}
    assert achados[0].impedimento_tipo


def test_sem_impedimento_nao_ha_conflito(db):
    _cenario(db)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 14))
    assert conflitos.conflitos(db) == []


def test_conflito_fora_da_janela_de_datas_nao_aparece(db):
    _cenario(db)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 14))
    _impede(db, 1, date(2026, 8, 1), date(2026, 8, 31))
    assert conflitos.conflitos(db, de=date(2026, 9, 1)) == []


def test_impedimento_sem_servico_gravado_nao_e_conflito(db):
    """Dispensa lançada para mês ainda não fechado: nada a desfazer."""
    _cenario(db)
    _impede(db, 1, date(2026, 8, 3), date(2026, 8, 14))
    assert conflitos.conflitos(db) == []


# --- proposta de substituto --------------------------------------------------

def test_substituto_proposto_e_o_proximo_da_fila(db):
    """Só o dia 3 fechado: ninguém mais serviu, e a fila desempata por
    antiguidade — o mais moderno depois de M1 é M2 (regra 9.5)."""
    _cenario(db)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    _impede(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    c = conflitos.conflitos(db)[0]
    assert c.motivo_sem_substituto is None
    assert c.substituto is not None and c.substituto.id == 2


def test_substituto_respeita_o_servico_que_ele_ja_tem_a_frente(db):
    """A guarda que só existe na substituição: o futuro já está gravado.

    M1 serve dia 3, M2 dia 4, M3 dia 5 (folga 48h). Impedindo M1 no dia 3, nem M2
    nem M3 podem assumir: ambos servem dentro das 48h seguintes.
    """
    _cenario(db)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 5))
    assert _escalados(db) == {date(2026, 8, 3): 1, date(2026, 8, 4): 2, date(2026, 8, 5): 3}
    _impede(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    c = conflitos.conflitos(db)[0]
    assert c.substituto is None
    assert "folga" in c.motivo_sem_substituto


def test_substituto_pula_quem_esta_impedido_e_pega_o_seguinte(db):
    """M2 é o próximo da fila, mas também está dispensado: desce para M3."""
    _cenario(db)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    _impede(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    _impede(db, 2, date(2026, 8, 3), date(2026, 8, 3))
    c = [c for c in conflitos.conflitos(db) if c.militar_id == 1][0]
    assert c.substituto is not None and c.substituto.id == 3


def test_sem_ninguem_livre_o_motivo_e_explicado(db):
    """Toda a escala impedida no dia: a tela precisa dizer POR QUE não há troca."""
    _cenario(db)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    for mid in (1, 2, 3):
        _impede(db, mid, date(2026, 8, 3), date(2026, 8, 3))
    c = [c for c in conflitos.conflitos(db) if c.militar_id == 1][0]
    assert c.substituto is None
    assert c.motivo_sem_substituto and "impedidos" in c.motivo_sem_substituto


def test_substituto_nao_dobra_com_outro_posto_do_mesmo_dia(db):
    """2 postos no mesmo dia: quem já está num não pode ser proposto no outro."""
    for i, antig in [(1, 30), (2, 20), (3, 10)]:
        _militar(db, i, antig)
    _escala(db, 1, "Guarda", postos=2, folga=48)
    _participa(db, 1, 1, 2, 3)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    escalados_no_dia = {s.militar_id for s in db.scalars(select(Servico))}
    assert len(escalados_no_dia) == 2
    impedido = min(escalados_no_dia)
    _impede(db, impedido, date(2026, 8, 3), date(2026, 8, 3))
    c = conflitos.conflitos(db)[0]
    assert c.substituto is None or c.substituto.id not in escalados_no_dia


def test_substituto_nao_dobra_com_escala_concorrente(db):
    _cenario(db)
    _escala(db, 2, "Museu", postos=1, folga=48)
    _participa(db, 2, 1, 2, 3)
    db.add(EscalaConcorrente(escala_menor_id=1, escala_maior_id=2))
    db.flush()
    # M1 na escala 1 e M2 na escala 2, no mesmo dia
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    rotacao.escalar_e_gravar_periodo(db, 2, date(2026, 8, 3), date(2026, 8, 3))
    ocupados = {s.militar_id for s in db.scalars(select(Servico).where(Servico.dia == date(2026, 8, 3)))}
    _impede(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    for c in conflitos.conflitos(db):
        assert c.substituto is None or c.substituto.id not in ocupados


def test_substituto_respeita_a_restricao_de_cor(db):
    """Regra 3.3.1: quem só concorre na vermelha não é proposto num dia preto."""
    for i, antig in [(1, 30), (2, 20)]:
        _militar(db, i, antig)
    _escala(db, 1, "Oficial de Dia", postos=1, folga=48)
    _participa(db, 1, 1)
    _participa(db, 1, 2, serve_preta=False)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 3))  # segunda = preta
    assert db.scalar(select(Servico.cor)) is Cor.PRETA
    _impede(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    c = conflitos.conflitos(db)[0]
    assert c.substituto is None


# --- a troca -----------------------------------------------------------------

def test_substituir_troca_o_escalado_mantendo_posto_e_dia(db):
    _cenario(db)
    _militar(db, 4, 5)
    _participa(db, 1, 4)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    servico = db.scalar(select(Servico))
    posto_antes, dia_antes, id_antes = servico.posto_id, servico.dia, servico.id

    conflitos.substituir(db, servico.id, 4)

    s = db.get(Servico, id_antes)
    assert s.militar_id == 4
    assert (s.posto_id, s.dia) == (posto_antes, dia_antes)
    assert db.scalar(select(Servico.id).where(Servico.dia == dia_antes)) == id_antes


def test_substituir_nao_muda_os_demais_dias(db):
    """O ponto da demanda: resolver o conflito sem refazer o mês publicado."""
    _cenario(db)
    _militar(db, 4, 5)
    _participa(db, 1, 4)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 14))
    antes = _escalados(db)
    dia_alvo = [d for d, m in antes.items() if m == 1][0]
    servico = db.scalar(select(Servico).where(Servico.dia == dia_alvo))
    _impede(db, 1, dia_alvo, dia_alvo)

    c = [c for c in conflitos.conflitos(db) if c.dia == dia_alvo][0]
    if c.substituto is None:
        pytest.skip("cenário sem substituto viável")
    conflitos.substituir(db, servico.id, c.substituto.id)

    depois = _escalados(db)
    assert depois[dia_alvo] == c.substituto.id
    assert {d: m for d, m in depois.items() if d != dia_alvo} == \
           {d: m for d, m in antes.items() if d != dia_alvo}


def test_substituir_recusa_quem_nao_participa(db):
    _cenario(db)
    _militar(db, 9, 1)      # existe, mas não participa da escala
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    servico = db.scalar(select(Servico))
    with pytest.raises(conflitos.SubstituicaoNegada, match="participante"):
        conflitos.substituir(db, servico.id, 9)


def test_substituir_recusa_impedido(db):
    _cenario(db)
    _militar(db, 4, 5)
    _participa(db, 1, 4)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    servico = db.scalar(select(Servico))
    _impede(db, 4, date(2026, 8, 3), date(2026, 8, 3))
    with pytest.raises(conflitos.SubstituicaoNegada, match="impedido"):
        conflitos.substituir(db, servico.id, 4)


def test_substituir_recusa_quem_perde_a_folga(db):
    """M2 serve no dia seguinte: pô-lo hoje o deixaria sem 48h entre os dois."""
    _cenario(db)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 4))
    servico = db.scalar(select(Servico).where(Servico.dia == date(2026, 8, 3)))
    with pytest.raises(conflitos.SubstituicaoNegada, match="folga"):
        conflitos.substituir(db, servico.id, 2)


def test_substituir_recusa_o_proprio_escalado(db):
    _cenario(db)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    servico = db.scalar(select(Servico))
    with pytest.raises(conflitos.SubstituicaoNegada, match="próprio"):
        conflitos.substituir(db, servico.id, servico.militar_id)


def test_substituir_recusa_servico_com_permuta(db):
    """A permuta é registro manual do gestor (regra 9); trocar o escalado a
    tornaria mentirosa — quem cobre está cobrindo por outra pessoa."""
    _cenario(db)
    _militar(db, 4, 5)
    _participa(db, 1, 4)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    servico = db.scalar(select(Servico))
    db.add(Permuta(servico_id=servico.id, militar_substituto_id=4))
    db.flush()
    with pytest.raises(conflitos.SubstituicaoNegada, match="permuta"):
        conflitos.substituir(db, servico.id, 4)


def test_conflito_marca_o_que_tem_permuta(db):
    _cenario(db)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    servico = db.scalar(select(Servico))
    outro = [i for i in (1, 2, 3) if i != servico.militar_id][0]
    db.add(Permuta(servico_id=servico.id, militar_substituto_id=outro))
    _impede(db, servico.militar_id, date(2026, 8, 3), date(2026, 8, 3))
    db.flush()
    assert conflitos.conflitos(db)[0].tem_permuta is True


def test_substituir_servico_inexistente(db):
    with pytest.raises(ValueError):
        conflitos.substituir(db, 999, 1)


# --- descobrir a vaga --------------------------------------------------------

def test_descobrir_apaga_o_servico(db):
    _cenario(db)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    servico = db.scalar(select(Servico))
    conflitos.descobrir(db, servico.id)
    assert db.scalar(select(Servico)) is None


def test_descobrir_resolve_o_conflito(db):
    _cenario(db)
    rotacao.escalar_e_gravar_periodo(db, 1, date(2026, 8, 3), date(2026, 8, 3))
    servico = db.scalar(select(Servico))
    _impede(db, servico.militar_id, date(2026, 8, 3), date(2026, 8, 3))
    assert len(conflitos.conflitos(db)) == 1
    conflitos.descobrir(db, servico.id)
    assert conflitos.conflitos(db) == []
