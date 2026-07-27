"""Regressões dos achados do /code-review (2026-07-25).

#1 'Regravar' um período apagava as permutas em silêncio (ON DELETE CASCADE).
#2 /gestao/permutas com mês fora de 1..12 devolvia 500.
#3 `ano` sem faixa na CONSULTA ABERTA (/ e /escalas/{id}/impressao) devolvia 500.
#4 Importar a ficha na EDIÇÃO acusava o próprio militar como duplicata.
#5 CPF/identidade digitados com máscara escapavam do UNIQUE (cadastro dobrado).
#6 A tela anunciava os serviços PRETENDIDOS, não os gravados.
#7 'Ler ficha e preencher' descartava o que já estava digitado no cadastro novo.
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.escala import Escala, Participacao, Posto
from app.models.gestao import Auditoria
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.models.servico import Permuta, Servico
from app.seeds import seed_circulos, seed_postos_graduacao
from app.seeds.usuario import criar_ou_atualizar_gestor
from tests.test_ficha_importacao import ficha_sicapex


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
    # a ficha sintética traz esta OM; a sigla precisa casar para reconciliar
    s.add(OrganizacaoMilitar(id=1, nome="Comando Militar do Sul", sigla="Cmdo CMS"))
    criar_ou_atualizar_gestor(s, "brigada", "senha-boa-123", "Sgt Brigada")
    s.flush()
    sgt = s.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "2º Sgt"))
    for i in (1, 2):
        s.add(Militar(id=i, nome_guerra=f"M{i}", nome_completo=f"Militar {i}",
                      posto_graduacao_id=sgt, om_id=1, numero_antiguidade=100 - i * 10))
    s.add(Escala(id=1, nome="Oficial de Dia", folga_minima_horas=24))
    s.flush()
    s.add(Posto(escala_id=1, ordem=1))
    s.add(Participacao(militar_id=1, escala_id=1))
    s.add(Participacao(militar_id=2, escala_id=1))
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


def _escalar(client, regravar=False):
    dados = {"escala_id": 1, "inicio": "2026-07-20", "fim": "2026-07-21"}
    if regravar:
        dados["regravar"] = "1"
    return client.post("/gestao/escalar", data=dados, follow_redirects=True)


def _pg_id(db, sigla="2º Sgt"):
    return db.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == sigla))


def _campos(db, **over):
    v = {"nome_guerra": "Novo", "nome_completo": "Militar Novo",
         "posto_graduacao_id": _pg_id(db), "om_id": 1,
         "identidade": "", "cpf": "", "data_promocao": "", "data_praca": "",
         "data_nascimento": "", "numero_antiguidade": ""}
    v.update(over)
    return v


# --- #1: 'regravar' apaga permutas ---
def test_regravar_avisa_e_audita_as_permutas_apagadas(client, db):
    _escalar(client)
    servico = db.scalars(select(Servico).order_by(Servico.dia)).first()
    escalado = servico.militar_id
    substituto = 2 if escalado == 1 else 1
    db.add(Permuta(servico_id=servico.id, militar_substituto_id=substituto,
                   observacao="troca combinada"))
    db.commit()

    r = _escalar(client, regravar=True)
    assert r.status_code == 200
    # o gestor é avisado do que perdeu — permuta é registro manual dele (regra 9)
    assert "permuta(s) do período foram apagadas" in r.text
    assert db.scalar(select(func.count()).select_from(Permuta)) == 0

    reg = db.scalars(
        select(Auditoria).where(Auditoria.acao == "escalar").order_by(Auditoria.id.desc())
    ).first()
    apagadas = reg.dados_depois["permutas_apagadas"]
    assert len(apagadas) == 1
    assert apagadas[0]["escalado_id"] == escalado
    assert apagadas[0]["substituto_id"] == substituto
    assert apagadas[0]["dia"] == servico.dia.isoformat()


def test_escalar_sem_regravar_nao_reporta_permutas_apagadas(client, db):
    r = _escalar(client)
    assert "foram apagadas" not in r.text
    reg = db.scalar(select(Auditoria).where(Auditoria.acao == "escalar"))
    assert reg.dados_depois["permutas_apagadas"] == []


# --- #6: contar o que foi GRAVADO, não o que se pretendia ---
def test_reescalar_sem_regravar_anuncia_zero_gravados(client, db):
    _escalar(client)
    gravados = db.scalar(select(func.count()).select_from(Servico))
    assert gravados > 0

    r = _escalar(client)   # de novo, sem regravar: a gravação é idempotente
    assert "<strong>0</strong> serviços gravados" in r.text
    assert db.scalar(select(func.count()).select_from(Servico)) == gravados
    reg = db.scalars(
        select(Auditoria).where(Auditoria.acao == "escalar").order_by(Auditoria.id.desc())
    ).first()
    assert reg.dados_depois["servicos_gravados"] == 0
    assert reg.dados_depois["servicos_intencionados"] == gravados


# --- #2: mês inválido na tela de permutas ---
@pytest.mark.parametrize("mes", [0, 13, 99])
def test_permutas_mes_fora_da_faixa_422(client, mes):
    r = client.get(f"/gestao/permutas?escala_id=1&ano=2026&mes={mes}")
    assert r.status_code == 422    # antes: 500 em date(ano, mes, 1)


def test_permutas_mes_valido_ok(client):
    assert client.get("/gestao/permutas?escala_id=1&ano=2026&mes=7").status_code == 200


# --- #3: ano sem faixa na consulta ABERTA (regra 13.1) ---
@pytest.mark.parametrize("rota", ["/?escala_id=1&mes=1", "/escalas/1/impressao?mes=1"])
def test_ano_fora_da_faixa_422_na_consulta_aberta(client, rota):
    assert client.get(f"{rota}&ano=0").status_code == 422       # antes: 500
    assert client.get(f"{rota}&ano=99999").status_code == 422


@pytest.mark.parametrize("rota", ["/?escala_id=1&mes=1", "/escalas/1/impressao?mes=1"])
def test_ano_valido_segue_aberto(client, rota):
    assert client.get(f"{rota}&ano=2026").status_code == 200


# --- #4: ficha na edição não acusa o próprio militar ---
def test_importar_ficha_na_edicao_nao_acusa_o_proprio_militar(client, db):
    m = Militar(nome_guerra="FULANO", nome_completo="Fulano de Tal da Silva",
                identidade="0311111111", cpf="60512636087",
                posto_graduacao_id=_pg_id(db), om_id=1)
    db.add(m)
    db.commit()
    r = client.post(f"/gestao/militares/{m.id}/importar",
                    files={"arquivo": ("f.pdf", ficha_sicapex(), "application/pdf")})
    assert r.status_code == 200
    assert "já pertence a" not in r.text
    assert "Já existe militar com este nome de guerra" not in r.text


def test_importar_ficha_no_cadastro_novo_ainda_acusa_duplicata(client, db):
    """A proteção continua valendo onde ela importa: criar um SEGUNDO cadastro."""
    db.add(Militar(nome_guerra="Ciclano", nome_completo="Ciclano de Tal",
                   identidade="0311111111", posto_graduacao_id=_pg_id(db), om_id=1))
    db.commit()
    r = client.post("/gestao/militares/importar",
                    files={"arquivo": ("f.pdf", ficha_sicapex(), "application/pdf")})
    assert "já pertence a Ciclano" in r.text


# --- #5: CPF/identidade normalizados no formulário ---
def test_form_grava_cpf_e_identidade_so_com_digitos(client, db):
    r = client.post("/gestao/militares",
                    data=_campos(db, cpf="605.126.360-87", identidade="03.111.111-0"),
                    follow_redirects=False)
    assert r.status_code == 303
    m = db.scalar(select(Militar).where(Militar.nome_guerra == "Novo"))
    assert m.cpf == "60512636087"
    assert m.identidade == "031111110"


def test_form_com_mascara_nao_dobra_cadastro(client, db):
    client.post("/gestao/militares", data=_campos(db, cpf="60512636087"),
                follow_redirects=False)
    # a mesma pessoa, agora digitada com máscara: tem de bater no UNIQUE
    r = client.post("/gestao/militares",
                    data=_campos(db, nome_guerra="Outro", cpf="605.126.360-87"),
                    follow_redirects=False)
    assert r.status_code == 409
    assert db.scalar(select(func.count()).select_from(Militar)
                     .where(Militar.cpf == "60512636087")) == 1


def test_form_recusa_documento_sem_nenhum_digito(client, db):
    r = client.post("/gestao/militares", data=_campos(db, cpf="abc"))
    assert r.status_code == 400 and "deve conter dígitos" in r.text


# --- #7: importar não descarta o que já foi digitado ---
def test_importar_no_cadastro_novo_preserva_o_digitado(client, db):
    """O nº de antiguidade (regra 9.5) NÃO vem na ficha: se a importação o
    descartar, o operador digita de novo sem perceber que sumiu."""
    r = client.post(
        "/gestao/militares/importar",
        data=_campos(db, nome_guerra="", nome_completo="", numero_antiguidade="42"),
        files={"arquivo": ("f.pdf", ficha_sicapex(), "application/pdf")},
    )
    assert r.status_code == 200
    assert 'value="42"' in r.text                  # o digitado sobreviveu
    assert "FULANO DE TAL DA SILVA" in r.text      # e a ficha preencheu o resto


def test_importar_sem_arquivo_avisa_em_vez_de_estourar(client, db):
    """O campo de arquivo vive no formulário principal (não pode ser `required`,
    senão travaria o Salvar), então submeter sem PDF é caso normal."""
    r = client.post("/gestao/militares/importar", data=_campos(db))
    assert r.status_code == 400 and "Selecione o arquivo PDF" in r.text
