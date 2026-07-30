"""Lançamento manual de serviço (demanda do Brigada, 30/07).

"Poder alterar a data em que o militar tirou o último serviço; o sistema deve
considerar essa data para contagem de folga." Não há campo de 'último serviço':
a fila e a folga são DERIVADAS de `servico`. O que estes testes provam é
justamente isso — que escrever na tabela pela mão do gestor move a fila e a
folga sem nenhuma linha nova no motor.

Postura de fato consumado, como na importação de CSV: folga curta, militar não
participante e cor errada são AVISO; vaga ocupada e dobra no mesmo dia são ERRO.
"""
from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.domain.models import Cor
from app.main import app
from app.models.calendario import Feriado
from app.models.escala import Escala, EscalaConcorrente, Participacao, Posto
from app.models.gestao import Auditoria
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao, TipoImpedimento
from app.models.servico import Permuta, Servico
from app.seeds import seed_circulos, seed_postos_graduacao
from app.seeds.usuario import criar_ou_atualizar_gestor
from app.services import lancamento, mapeamento


def _segunda_passada() -> date:
    """Uma segunda-feira no PASSADO, relativa a hoje.

    Data fixa não serve aqui: o lançamento avisa quando a data está no futuro
    (é registro do que já aconteceu), e um `date(2026, 8, 3)` cravado passaria a
    disparar — ou a não disparar — esse aviso conforme o dia em que a suíte roda.
    """
    d = date.today() - timedelta(days=7)
    return d - timedelta(days=d.weekday())


SEG = _segunda_passada()            # segunda -> preta
SAB = SEG + timedelta(days=5)       # sábado  -> vermelha
MES = {"ano": SEG.year, "mes": SEG.month}


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
    s.add(OrganizacaoMilitar(id=1, nome="Quartel General", sigla="QG"))
    s.add(TipoImpedimento(id=1, nome="Dispensa"))
    criar_ou_atualizar_gestor(s, "brigada", "senha-boa-123", "Sgt Brigada")
    s.commit()
    yield s
    s.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def logado(client):
    client.post("/gestao/login", data={"username": "brigada", "password": "senha-boa-123"},
                follow_redirects=False)
    return client


def _militar(db, nome):
    m = Militar(nome_guerra=nome, nome_completo=f"{nome} de Tal", om_id=1,
                posto_graduacao_id=db.scalar(
                    select(PostoGraduacao.id).where(PostoGraduacao.sigla == "Cap")))
    db.add(m)
    db.flush()
    return m


@pytest.fixture()
def cenario(db):
    """Escala de 1 posto, folga 48h, com SILVA e ROANA participando."""
    e = Escala(nome="Oficial de Dia", folga_minima_horas=48)
    e.postos = [Posto(ordem=1, rotulo="Serviço")]
    db.add(e)
    db.flush()
    silva, roana = _militar(db, "SILVA"), _militar(db, "ROANA")
    db.add_all([Participacao(militar_id=silva.id, escala_id=e.id),
                Participacao(militar_id=roana.id, escala_id=e.id)])
    db.commit()
    return e, e.postos[0], silva, roana


def _form(**extra) -> dict:
    """Corpo do formulário com o mês da tela (usado no redirect e no re-render)."""
    return {**MES, **extra}


# --- o que a demanda pede: a folga passa a considerar a data lançada ---------

def test_servico_lancado_a_mao_conta_para_a_folga(db, cenario):
    """O coração do pedido. Sem o lançamento, SILVA aparece como 'nunca serviu'."""
    e, posto, silva, _ = cenario
    escala = mapeamento.escala_para_dominio(db, e)
    assert mapeamento.ultimo_termino_por_militar(
        db, escala, [silva.id], antes_de_dia=SEG + timedelta(days=5)) == {}

    lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    db.commit()

    ultimo = mapeamento.ultimo_termino_por_militar(
        db, escala, [silva.id], antes_de_dia=SEG + timedelta(days=5))
    assert ultimo[silva.id] == datetime.combine(SEG, time(8, 0)) + timedelta(hours=24)


def test_servico_lancado_a_mao_entra_na_fila_da_cor(db, cenario):
    e, posto, silva, _ = cenario
    lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    db.commit()
    parts = mapeamento.participacoes_da_escala(db, e.id, ate_dia=SEG + timedelta(days=5))
    de_silva = next(p for p in parts if p.militar.id == silva.id)
    assert de_silva.ultima_preta == SEG
    assert de_silva.ultima_vermelha is None


def test_alterar_a_data_move_a_folga(db, cenario):
    """"Alterar a data em que o militar tirou o último serviço", literalmente."""
    e, posto, silva, _ = cenario
    s = lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    db.commit()
    escala = mapeamento.escala_para_dominio(db, e)
    alvo = SEG + timedelta(days=10)

    nova_data = SEG + timedelta(days=2)
    lancamento.alterar(db, s.id, nova_data, silva.id)
    db.commit()

    ultimo = mapeamento.ultimo_termino_por_militar(db, escala, [silva.id], antes_de_dia=alvo)
    assert ultimo[silva.id] == datetime.combine(nova_data, time(8, 0)) + timedelta(hours=24)


# --- a cor vem do calendário ------------------------------------------------

def test_cor_sai_do_calendario_e_nao_de_quem_digita(db, cenario):
    e, posto, silva, _ = cenario
    preta = lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    assert preta.cor is Cor.PRETA
    db.commit()
    vermelha = lancamento.lancar(db, e.id, posto.id, SAB, silva.id)
    assert vermelha.cor is Cor.VERMELHA


def test_feriado_da_om_torna_o_dia_vermelho(db, cenario):
    e, posto, silva, _ = cenario
    db.add(Feriado(data=SEG, nome="Aniversário da OM"))
    db.commit()
    assert lancamento.lancar(db, e.id, posto.id, SEG, silva.id).cor is Cor.VERMELHA


def test_mudar_a_data_recalcula_a_cor_e_a_janela(db, cenario):
    """Serviço movido de segunda para sábado é VERMELHO — guardar a cor antiga
    poria o militar na fila errada."""
    e, posto, silva, _ = cenario
    s = lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    db.commit()
    lancamento.alterar(db, s.id, SAB, silva.id)
    assert s.cor is Cor.VERMELHA
    assert s.inicio_dt == datetime.combine(SAB, time(8, 0))
    assert s.termino_dt == datetime.combine(SAB, time(8, 0)) + timedelta(hours=24)


# --- erros: impedem ---------------------------------------------------------

def test_vaga_ja_ocupada_e_erro(db, cenario):
    e, posto, silva, roana = cenario
    lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    db.commit()
    a = lancamento.analisar(db, e.id, posto.id, SEG, roana.id)
    assert not a.pode_gravar
    assert any("já tem serviço" in m for m in a.erros)
    with pytest.raises(lancamento.LancamentoNegado):
        lancamento.lancar(db, e.id, posto.id, SEG, roana.id)


def test_dobra_no_mesmo_dia_em_escala_concorrente_e_erro(db, cenario):
    e, posto, silva, _ = cenario
    outra = Escala(nome="Museu", folga_minima_horas=48)
    outra.postos = [Posto(ordem=1, rotulo="Museu")]
    db.add(outra)
    db.flush()
    db.add(EscalaConcorrente(escala_menor_id=min(e.id, outra.id),
                             escala_maior_id=max(e.id, outra.id)))
    lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    db.commit()
    a = lancamento.analisar(db, outra.id, outra.postos[0].id, SEG, silva.id)
    assert not a.pode_gravar
    assert any("dois turnos" in m for m in a.erros)


def test_posto_de_outra_escala_e_erro(db, cenario):
    e, posto, silva, _ = cenario
    outra = Escala(nome="Museu")
    outra.postos = [Posto(ordem=1, rotulo="Museu")]
    db.add(outra)
    db.commit()
    a = lancamento.analisar(db, e.id, outra.postos[0].id, SEG, silva.id)
    assert any("não pertence" in m for m in a.erros)


def test_entidades_inexistentes_sao_erro(db, cenario):
    e, posto, silva, _ = cenario
    assert lancamento.analisar(db, 999, posto.id, SEG, silva.id).erros
    assert lancamento.analisar(db, e.id, posto.id, SEG, 999).erros


# --- avisos: deixam passar --------------------------------------------------

def test_folga_curta_e_aviso_e_nao_recusa(db, cenario):
    """Fato consumado: se o militar serviu dois dias seguidos na emergência,
    recusar impediria de registrar exatamente o que se quer registrar."""
    e, posto, silva, _ = cenario
    lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    db.commit()
    a = lancamento.analisar(db, e.id, posto.id, SEG + timedelta(days=1), silva.id)
    assert a.pode_gravar
    assert any("Folga menor" in m for m in a.avisos)
    assert lancamento.lancar(db, e.id, posto.id, SEG + timedelta(days=1), silva.id)


def test_nao_participante_e_aviso(db, cenario):
    e, posto, _, _ = cenario
    forasteiro = _militar(db, "ALHEIO")
    db.commit()
    a = lancamento.analisar(db, e.id, posto.id, SEG, forasteiro.id)
    assert a.pode_gravar
    assert any("não é participante" in m for m in a.avisos)


def test_participante_isento_e_aviso(db, cenario):
    e, posto, silva, _ = cenario
    p = db.scalar(select(Participacao).where(Participacao.militar_id == silva.id))
    p.ativo = False
    db.commit()
    a = lancamento.analisar(db, e.id, posto.id, SEG, silva.id)
    assert a.pode_gravar and any("isento" in m for m in a.avisos)


def test_cor_que_o_militar_nao_concorre_e_aviso(db, cenario):
    e, posto, silva, _ = cenario
    p = db.scalar(select(Participacao).where(Participacao.militar_id == silva.id))
    p.serve_preta = False
    db.commit()
    a = lancamento.analisar(db, e.id, posto.id, SEG, silva.id)
    assert a.pode_gravar and any("não concorre" in m for m in a.avisos)


def test_impedimento_no_dia_e_aviso(db, cenario):
    e, posto, silva, _ = cenario
    db.add(Impedimento(militar_id=silva.id, tipo_impedimento_id=1, inicio=SEG, fim=SEG))
    db.commit()
    a = lancamento.analisar(db, e.id, posto.id, SEG, silva.id)
    assert a.pode_gravar and any("impedimento" in m for m in a.avisos)


def test_data_no_futuro_e_aviso(db, cenario):
    e, posto, silva, _ = cenario
    a = lancamento.analisar(db, e.id, posto.id, SEG, silva.id, hoje=SEG - timedelta(days=1))
    assert a.pode_gravar and any("futuro" in m for m in a.avisos)


def test_sem_pendencia_nenhuma_nao_ha_aviso(db, cenario):
    e, posto, silva, _ = cenario
    a = lancamento.analisar(db, e.id, posto.id, SEG, silva.id, hoje=SEG)
    assert a.pode_gravar and a.avisos == []


# --- editar a própria linha -------------------------------------------------

def test_editar_so_o_militar_nao_acusa_vaga_ocupada(db, cenario):
    """A guarda `ignorar_servico_id`: sem ela a própria linha se acusaria."""
    e, posto, silva, roana = cenario
    s = lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    db.commit()
    a = lancamento.analisar(db, e.id, posto.id, SEG, roana.id, ignorar_servico_id=s.id)
    assert a.pode_gravar
    lancamento.alterar(db, s.id, SEG, roana.id)
    assert db.get(Servico, s.id).militar_id == roana.id


def test_alterar_servico_com_permuta_e_negado(db, cenario):
    e, posto, silva, roana = cenario
    s = lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    db.add(Permuta(servico_id=s.id, militar_substituto_id=roana.id))
    db.commit()
    with pytest.raises(lancamento.LancamentoNegado, match="permuta"):
        lancamento.alterar(db, s.id, SAB, silva.id)


def test_remover_apaga(db, cenario):
    e, posto, silva, _ = cenario
    s = lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    db.commit()
    lancamento.remover(db, s.id)
    assert db.get(Servico, s.id) is None


def test_alterar_servico_inexistente(db):
    with pytest.raises(ValueError):
        lancamento.alterar(db, 999, SEG, 1)


# --- telas ------------------------------------------------------------------

def test_tela_exige_login(client):
    r = client.get("/gestao/servicos", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/gestao/login" in r.headers["location"]


def test_tela_abre_com_a_escala_e_o_mes(logado, cenario):
    r = logado.get(f"/gestao/servicos?escala_id={cenario[0].id}"
                   f"&ano={SEG.year}&mes={SEG.month}")
    assert r.status_code == 200
    assert "Oficial de Dia" in r.text


def test_mes_invalido_cai_no_corrente_sem_estourar(logado, cenario):
    """Link de mês colado errado não pode virar 422 na cara do gestor."""
    assert logado.get("/gestao/servicos?ano=0&mes=99").status_code == 200


def test_lancar_sem_pendencia_grava_e_audita(logado, db, cenario):
    e, posto, silva, _ = cenario
    r = logado.post("/gestao/servicos", data=_form(
        escala_id=e.id, posto_id=posto.id, dia=SEG.isoformat(), militar_id=silva.id,
    ), follow_redirects=False)
    assert r.status_code == 303
    assert "ok=servico-lancado" in r.headers["location"]
    s = db.scalar(select(Servico))
    assert s is not None and s.militar_id == silva.id
    reg = db.scalar(select(Auditoria).where(Auditoria.acao == "lancar"))
    assert reg.dados_depois["dia"] == SEG.isoformat()


def test_aviso_segura_o_lancamento_ate_confirmar(logado, db, cenario):
    """Etapa 1: mostra o que preocupa e NÃO grava."""
    e, posto, _, _ = cenario
    forasteiro = _militar(db, "ALHEIO")
    db.commit()
    r = logado.post("/gestao/servicos", data=_form(
        escala_id=e.id, posto_id=posto.id, dia=SEG.isoformat(), militar_id=forasteiro.id,
    ))
    assert r.status_code == 200
    assert "não é participante" in r.text
    assert "Gravar assim mesmo" in r.text
    assert db.scalar(select(Servico)) is None


def test_confirmado_grava_apesar_do_aviso(logado, db, cenario):
    e, posto, _, _ = cenario
    forasteiro = _militar(db, "ALHEIO")
    db.commit()
    r = logado.post("/gestao/servicos", data=_form(
        escala_id=e.id, posto_id=posto.id, dia=SEG.isoformat(),
        militar_id=forasteiro.id, confirmado="1",
    ), follow_redirects=False)
    assert r.status_code == 303
    assert db.scalar(select(Servico)) is not None


def test_erro_nao_ganha_botao_de_confirmar(logado, db, cenario):
    """Confirmar não pode contornar o que o banco recusaria."""
    e, posto, silva, roana = cenario
    lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    db.commit()
    r = logado.post("/gestao/servicos", data=_form(
        escala_id=e.id, posto_id=posto.id, dia=SEG.isoformat(), militar_id=roana.id,
    ))
    assert r.status_code == 400
    assert "Gravar assim mesmo" not in r.text


def test_confirmado_nao_atropela_erro(logado, db, cenario):
    e, posto, silva, roana = cenario
    lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    db.commit()
    r = logado.post("/gestao/servicos", data=_form(
        escala_id=e.id, posto_id=posto.id, dia=SEG.isoformat(),
        militar_id=roana.id, confirmado="1",
    ))
    assert r.status_code == 400
    assert db.scalar(select(Servico).where(Servico.militar_id == roana.id)) is None


def test_data_ilegivel_no_formulario_e_mensagem(logado, cenario):
    e, posto, silva, _ = cenario
    r = logado.post("/gestao/servicos", data=_form(
        escala_id=e.id, posto_id=posto.id, dia="trinta de julho", militar_id=silva.id,
    ))
    assert r.status_code == 400
    assert "Data inválida" in r.text


def test_corrigir_pela_tela_grava_e_audita(logado, db, cenario):
    e, posto, silva, _ = cenario
    s = lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    db.commit()
    nova = SEG + timedelta(days=2)
    r = logado.post(f"/gestao/servicos/{s.id}/alterar", data=_form(
        dia=nova.isoformat(), militar_id=silva.id, posto_id=posto.id, confirmado="1",
    ), follow_redirects=False)
    assert r.status_code == 303
    assert "ok=servico-alterado" in r.headers["location"]
    db.expire_all()
    assert db.get(Servico, s.id).dia == nova
    reg = db.scalar(select(Auditoria).where(Auditoria.acao == "alterar",
                                            Auditoria.entidade == "servico"))
    assert reg.dados_antes["dia"] == SEG.isoformat()
    assert reg.dados_depois["dia"] == nova.isoformat()


def test_remover_pela_tela_audita(logado, db, cenario):
    e, posto, silva, _ = cenario
    s = lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    db.commit()
    r = logado.post(f"/gestao/servicos/{s.id}/remover", data=MES, follow_redirects=False)
    assert r.status_code == 303
    assert "ok=servico-removido" in r.headers["location"]
    assert db.get(Servico, s.id) is None
    assert db.scalar(select(Auditoria).where(Auditoria.acao == "excluir",
                                             Auditoria.entidade == "servico")) is not None


def test_linha_com_permuta_nao_oferece_correcao(logado, db, cenario):
    e, posto, silva, roana = cenario
    s = lancamento.lancar(db, e.id, posto.id, SEG, silva.id)
    db.add(Permuta(servico_id=s.id, militar_substituto_id=roana.id))
    db.commit()
    r = logado.get(f"/gestao/servicos?escala_id={e.id}&ano={SEG.year}&mes={SEG.month}")
    assert "tem permuta" in r.text
    assert f"/gestao/servicos?editar={s.id}" not in r.text


def test_servico_inexistente_volta_para_a_lista(logado):
    r = logado.post("/gestao/servicos/999999/remover", data=MES, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/gestao/servicos"


def test_avisos_usados_tem_traducao():
    from app.web import AVISOS
    for chave in ("servico-lancado", "servico-alterado", "servico-removido"):
        assert chave in AVISOS
