"""Telas de gestão da ESCALA e do CALENDÁRIO (HTML, protegidas — regra 11).

Fecham pela interface o que só existia na API JSON. Cobre: CRUD da escala
(regra 4), postos (2.5), participantes/isenção (3.3 e 7.6), concorrência
(7.4.1), feriados (5.2) e override de cor do dia (5.3).
"""
from datetime import date, datetime, time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.domain.models import Cor
from app.main import app
from app.models.calendario import Feriado, OverrideDia
from app.models.escala import Escala, EscalaConcorrente, Participacao, Posto
from app.models.gestao import Auditoria
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.models.servico import Servico
from app.seeds import seed_circulos, seed_postos_graduacao
from app.seeds.usuario import criar_ou_atualizar_gestor


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
    criar_ou_atualizar_gestor(s, "brigada", "senha-boa-123", "Sgt Brigada")
    s.flush()
    sgt = s.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "2º Sgt"))
    for i in (1, 2, 3):
        s.add(Militar(id=i, nome_guerra=f"M{i}", nome_completo=f"Militar {i}",
                      posto_graduacao_id=sgt, om_id=1, numero_antiguidade=100 - i * 10))
    s.add(Escala(id=1, nome="Oficial de Dia", folga_minima_horas=48))
    s.add(Escala(id=2, nome="Museu", tem_preta=False, folga_minima_horas=24))
    s.flush()
    s.add(Posto(escala_id=1, ordem=1))
    s.add(Posto(escala_id=2, ordem=1))
    s.add(Participacao(militar_id=1, escala_id=1))
    s.commit()
    yield s
    s.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        c.post("/gestao/login", data={"username": "brigada", "password": "senha-boa-123"},
               follow_redirects=False)
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def anonimo(db):
    """Cliente sem sessão — a gestão não pode abrir para qualquer um (regra 11)."""
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _nova_escala(**over):
    dados = {"nome": "Permanência do Portão", "postos": "2", "folga_minima_horas": "48",
             "inicio_servico": "18:00", "duracao_horas": "14", "tem_preta": "1",
             "tem_vermelha": "1"}
    dados.update(over)
    return dados


# --- proteção (regra 11) ------------------------------------------------------
@pytest.mark.parametrize("rota", ["/gestao/escalas", "/gestao/escalas/nova",
                                  "/gestao/escalas/1", "/gestao/calendario"])
def test_telas_novas_exigem_sessao(anonimo, rota):
    r = anonimo.get(rota, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/gestao/login"


# --- escala: lista e criação --------------------------------------------------
def test_lista_mostra_escalas_ativas(client):
    r = client.get("/gestao/escalas")
    assert r.status_code == 200
    assert "Oficial de Dia" in r.text and "Museu" in r.text


def test_criar_escala_com_postos_e_audita(client, db):
    r = client.post("/gestao/escalas", data=_nova_escala(), follow_redirects=False)
    assert r.status_code == 303
    e = db.scalar(select(Escala).where(Escala.nome == "Permanência do Portão"))
    assert e is not None
    assert len(e.postos) == 2 and sorted(p.ordem for p in e.postos) == [1, 2]
    assert e.inicio_servico == time(18, 0) and e.duracao_horas == 14   # regra 2.4
    # leva à tela da escala JÁ com a confirmação da ação
    assert r.headers["location"] == f"/gestao/escalas/{e.id}?ok=escala-criada"
    reg = db.scalar(select(Auditoria).where(Auditoria.entidade == "escala",
                                            Auditoria.acao == "criar"))
    assert reg.dados_depois["nome"] == "Permanência do Portão"


def test_criar_escala_sem_nenhuma_cor_400(client, db):
    """Regra 4.5: sem cor a escala nunca escalaria ninguém."""
    r = client.post("/gestao/escalas",
                    data=_nova_escala(tem_preta=None, tem_vermelha=None))
    assert r.status_code == 400 and "ao menos uma cor" in r.text
    assert db.scalar(select(func.count()).select_from(Escala)) == 2   # nada criado


def test_criar_escala_com_folga_abaixo_do_piso_400(client, db):
    """Regra 7.2.1: 24h é piso rígido — 'saiu no dia X, não assume no dia X'."""
    r = client.post("/gestao/escalas", data=_nova_escala(folga_minima_horas="12"))
    assert r.status_code == 400 and "24h" in r.text
    assert db.scalar(select(func.count()).select_from(Escala)) == 2


def test_criar_escala_sem_nome_400(client):
    r = client.post("/gestao/escalas", data=_nova_escala(nome="  "))
    assert r.status_code == 400 and "nome" in r.text


# --- escala: alterar, extinguir, reativar ------------------------------------
def test_alterar_escala_e_audita(client, db):
    r = client.post("/gestao/escalas/1", follow_redirects=False, data={
        "nome": "Oficial de Dia ao QG", "tem_vermelha": "1",
        "folga_minima_horas": "72", "inicio_servico": "07:30", "duracao_horas": "24",
    })
    assert r.status_code == 303
    e = db.get(Escala, 1)
    assert e.nome == "Oficial de Dia ao QG" and e.folga_minima_horas == 72
    assert e.tem_preta is False and e.tem_vermelha is True   # checkbox desmarcado
    reg = db.scalar(select(Auditoria).where(Auditoria.entidade == "escala",
                                            Auditoria.acao == "alterar"))
    assert reg.dados_antes["nome"] == "Oficial de Dia"


def test_alterar_escala_nao_pode_zerar_as_cores(client, db):
    r = client.post("/gestao/escalas/1", data={
        "nome": "Oficial de Dia", "folga_minima_horas": "48",
        "inicio_servico": "08:00", "duracao_horas": "24",
    })
    assert r.status_code == 400 and "ao menos uma cor" in r.text
    assert db.get(Escala, 1).tem_preta is True   # nada mudou


def test_erro_de_validacao_devolve_o_que_foi_digitado(client):
    """Corrigir o erro não pode custar redigitar o formulário inteiro."""
    r = client.post("/gestao/escalas/1", data={
        "nome": "Nome Novo Que Nao Foi Salvo", "tem_preta": "1",
        "folga_minima_horas": "12", "inicio_servico": "07:30", "duracao_horas": "24",
    })
    assert r.status_code == 400
    assert "Nome Novo Que Nao Foi Salvo" in r.text
    assert 'value="07:30"' in r.text


def test_extinguir_e_reativar_escala(client, db):
    client.post("/gestao/escalas/1/extinguir", follow_redirects=False)
    assert db.get(Escala, 1).ativa is False
    # extinta some da lista padrão, mas o histórico e a escala continuam lá
    assert "Oficial de Dia" not in client.get("/gestao/escalas").text
    assert "Oficial de Dia" in client.get("/gestao/escalas?extintas=1").text

    client.post("/gestao/escalas/1/reativar", follow_redirects=False)
    assert db.get(Escala, 1).ativa is True


# --- postos (regra 2.5) -------------------------------------------------------
def test_acrescentar_posto_recebe_a_proxima_ordem(client, db):
    client.post("/gestao/escalas/1/postos", data={"rotulo": "Comandante da Guarda"},
                follow_redirects=False)
    postos = db.scalars(select(Posto).where(Posto.escala_id == 1).order_by(Posto.ordem)).all()
    assert [p.ordem for p in postos] == [1, 2]
    assert postos[1].rotulo == "Comandante da Guarda"


def test_renomear_posto(client, db):
    posto = db.scalar(select(Posto).where(Posto.escala_id == 1))
    client.post(f"/gestao/escalas/1/postos/{posto.id}", data={"rotulo": "Adjunto"},
                follow_redirects=False)
    assert db.get(Posto, posto.id).rotulo == "Adjunto"


def test_remover_posto_livre(client, db):
    client.post("/gestao/escalas/1/postos", data={"rotulo": ""}, follow_redirects=False)
    novo = db.scalars(select(Posto).where(Posto.escala_id == 1).order_by(Posto.ordem)).all()[-1]
    client.post(f"/gestao/escalas/1/postos/{novo.id}/remover", follow_redirects=False)
    assert db.get(Posto, novo.id) is None


def test_remover_posto_com_servico_gravado_e_barrado(client, db):
    """Apagar o posto levaria junto quem serviu nele — e a folga que daí decorre."""
    posto = db.scalar(select(Posto).where(Posto.escala_id == 1))
    db.add(Posto(escala_id=1, ordem=2))       # para não esbarrar no 'mínimo 1 posto'
    db.add(Servico(escala_id=1, posto_id=posto.id, militar_id=1, dia=date(2026, 7, 20),
                   cor=Cor.PRETA, inicio_dt=datetime(2026, 7, 20, 8),
                   termino_dt=datetime(2026, 7, 21, 8)))
    db.commit()
    r = client.post(f"/gestao/escalas/1/postos/{posto.id}/remover")
    assert r.status_code == 400 and "não pode ser removido" in r.text
    assert db.get(Posto, posto.id) is not None


def test_remover_o_ultimo_posto_e_barrado(client, db):
    posto = db.scalar(select(Posto).where(Posto.escala_id == 1))
    r = client.post(f"/gestao/escalas/1/postos/{posto.id}/remover")
    assert r.status_code == 400 and "pelo menos um posto" in r.text
    assert db.get(Posto, posto.id) is not None


# --- participantes (regras 3.3 e 7.6) ----------------------------------------
def test_incluir_participante(client, db):
    client.post("/gestao/escalas/1/participantes", data={"militar_id": "2"},
                follow_redirects=False)
    p = db.scalar(select(Participacao).where(Participacao.escala_id == 1,
                                             Participacao.militar_id == 2))
    assert p is not None and p.ativo is True


def test_isentar_desativa_sem_apagar_o_vinculo(client, db):
    """Isenção permanente = não-participação (regra 7.6); o vínculo fica para o
    histórico e para poder ser desfeita."""
    client.post("/gestao/escalas/1/participantes/1/isentar", follow_redirects=False)
    p = db.scalar(select(Participacao).where(Participacao.escala_id == 1,
                                             Participacao.militar_id == 1))
    assert p is not None and p.ativo is False
    assert db.scalar(select(Auditoria).where(Auditoria.entidade == "participacao",
                                             Auditoria.acao == "excluir")) is not None


def test_reincluir_isento_reaproveita_o_vinculo(client, db):
    client.post("/gestao/escalas/1/participantes/1/isentar", follow_redirects=False)
    client.post("/gestao/escalas/1/participantes", data={"militar_id": "1"},
                follow_redirects=False)
    vinculos = db.scalars(select(Participacao).where(Participacao.escala_id == 1,
                                                     Participacao.militar_id == 1)).all()
    assert len(vinculos) == 1 and vinculos[0].ativo is True   # não duplicou


def test_participante_ja_incluso_some_do_select(client):
    r = client.get("/gestao/escalas/1")
    # M1 já participa: aparece na tabela, mas não como opção para incluir de novo
    # os candidatos vêm agrupados por posto/graduação (optgroup)
    assert '<optgroup label="2º Sgt">' in r.text
    assert '<option value="2">M2</option>' in r.text
    assert '<option value="1">M1</option>' not in r.text


# --- concorrência (regra 7.4.1) ----------------------------------------------
def test_declarar_concorrencia_e_simetrica(client, db):
    client.post("/gestao/escalas/2/concorrentes", data={"outra_id": "1"},
                follow_redirects=False)
    # guardada uma vez, com o par ordenado
    assert db.get(EscalaConcorrente, (1, 2)) is not None
    # e visível dos dois lados
    assert "Museu" in client.get("/gestao/escalas/1").text
    assert "Oficial de Dia" in client.get("/gestao/escalas/2").text


def test_declarar_concorrencia_repetida_nao_duplica_auditoria(client, db):
    for _ in range(2):
        client.post("/gestao/escalas/1/concorrentes", data={"outra_id": "2"},
                    follow_redirects=False)
    regs = db.scalars(select(Auditoria).where(
        Auditoria.entidade == "escala_concorrente")).all()
    assert len(regs) == 1


def test_remover_concorrencia(client, db):
    client.post("/gestao/escalas/1/concorrentes", data={"outra_id": "2"},
                follow_redirects=False)
    client.post("/gestao/escalas/1/concorrentes/2/remover", follow_redirects=False)
    assert db.get(EscalaConcorrente, (1, 2)) is None


# --- calendário: feriados (5.2) ----------------------------------------------
def test_criar_feriado_da_om(client, db):
    r = client.post("/gestao/calendario/feriados",
                    data={"data": "2026-09-20", "nome": "Revolução Farroupilha"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/gestao/calendario?ano=2026&ok=feriado-criado"
    f = db.scalar(select(Feriado).where(Feriado.data == date(2026, 9, 20)))
    assert f.nome == "Revolução Farroupilha" and f.nacional is False


def test_feriado_em_data_repetida_409(client, db):
    db.add(Feriado(data=date(2026, 9, 20), nome="Farroupilha", nacional=False))
    db.commit()
    r = client.post("/gestao/calendario/feriados",
                    data={"data": "2026-09-20", "nome": "Outro nome"})
    assert r.status_code == 409 and "Já existe feriado" in r.text


def test_feriado_com_data_invalida_400(client):
    r = client.post("/gestao/calendario/feriados", data={"data": "", "nome": "X"})
    assert r.status_code == 400 and "Data inválida" in r.text


def test_remover_feriado(client, db):
    db.add(Feriado(id=99, data=date(2026, 9, 20), nome="Farroupilha", nacional=False))
    db.commit()
    client.post("/gestao/calendario/feriados/99/remover", follow_redirects=False)
    assert db.get(Feriado, 99) is None


# --- calendário: override de cor do dia (5.3) --------------------------------
def test_forcar_dia_como_vermelha(client, db):
    client.post("/gestao/calendario/overrides", follow_redirects=False, data={
        "data": "2026-08-10", "cor": "vermelha", "observacao": "ponto facultativo"})
    o = db.get(OverrideDia, date(2026, 8, 10))
    assert o.cor is Cor.VERMELHA and o.observacao == "ponto facultativo"


def test_forcar_feriado_como_preta(client, db):
    """O inverso da regra 5.3: feriado que a OM decidiu trabalhar."""
    client.post("/gestao/calendario/overrides", follow_redirects=False,
                data={"data": "2026-09-07", "cor": "preta", "observacao": ""})
    assert db.get(OverrideDia, date(2026, 9, 7)).cor is Cor.PRETA


def test_redefinir_override_do_mesmo_dia_atualiza(client, db):
    for cor in ("vermelha", "preta"):
        client.post("/gestao/calendario/overrides", follow_redirects=False,
                    data={"data": "2026-08-10", "cor": cor, "observacao": ""})
    assert db.scalar(select(func.count()).select_from(OverrideDia)) == 1
    assert db.get(OverrideDia, date(2026, 8, 10)).cor is Cor.PRETA


def test_override_com_cor_invalida_400(client, db):
    r = client.post("/gestao/calendario/overrides",
                    data={"data": "2026-08-10", "cor": "azul", "observacao": ""})
    assert r.status_code == 400 and "Cor inválida" in r.text
    assert db.scalar(select(func.count()).select_from(OverrideDia)) == 0


def test_remover_override(client, db):
    db.add(OverrideDia(data=date(2026, 8, 10), cor=Cor.VERMELHA))
    db.commit()
    client.post("/gestao/calendario/overrides/2026-08-10/remover", follow_redirects=False)
    assert db.get(OverrideDia, date(2026, 8, 10)) is None


def test_calendario_lista_por_ano(client, db):
    db.add(Feriado(data=date(2026, 9, 20), nome="Farroupilha", nacional=False))
    db.add(Feriado(data=date(2027, 9, 20), nome="Farroupilha", nacional=False))
    db.commit()
    assert "2026" in client.get("/gestao/calendario?ano=2026").text
    r = client.get("/gestao/calendario?ano=2025")
    assert "Nenhum feriado em 2025" in r.text
