"""Importação do histórico de serviços em CSV (carga de uma OM nova).

O que estes testes travam, em ordem de importância:
  - **nada é chutado**: o que não casa é recusado COM MOTIVO, nunca resolvido
    no palpite (mesma regra da importação da ficha em PDF);
  - **conferir não grava**; só o confirmar persiste;
  - serviço importado é FATO CONSUMADO — entra mesmo que hoje o militar não
    participe mais da escala, porque foi isso que aconteceu;
  - o arquivo que o Excel pt-BR gera (`;` e cp1252) é lido.
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
from app.models.calendario import Feriado
from app.models.escala import Escala, Participacao, Posto
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.models.servico import Servico
from app.seeds import seed_circulos, seed_postos_graduacao, seed_tipos_impedimento
from app.seeds.usuario import criar_ou_atualizar_gestor
from app.services import importacao_csv as csv_service

CABECALHO = "escala;data;militar;posto;om"


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
    s.add(OrganizacaoMilitar(id=2, nome="Batalhão de Apoio", sigla="B Ap"))
    criar_ou_atualizar_gestor(s, "brigada", "senha-boa-123", "Sgt Brigada")
    s.flush()
    cap = s.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "Cap"))
    s.add(Militar(id=1, nome_guerra="SOUZA", nome_completo="José de Souza",
                  posto_graduacao_id=cap, om_id=1))
    s.add(Militar(id=2, nome_guerra="LIMA", nome_completo="Ana Lima",
                  posto_graduacao_id=cap, om_id=1))
    # homônimo em outra OM: é o caso que obriga a coluna 'om'
    s.add(Militar(id=3, nome_guerra="SOUZA", nome_completo="Carlos Souza",
                  posto_graduacao_id=cap, om_id=2))
    # escala de uma vaga e escala de duas (a da guarda, que exige 'posto')
    s.add(Escala(id=1, nome="Oficial de Dia", folga_minima_horas=48))
    s.add(Escala(id=2, nome="Guarda", folga_minima_horas=48))
    s.flush()
    s.add(Posto(id=1, escala_id=1, ordem=1))
    s.add(Posto(id=2, escala_id=2, ordem=1, rotulo="Comandante da Guarda"))
    s.add(Posto(id=3, escala_id=2, ordem=2, rotulo="Cabo da Guarda"))
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


def _ler(db, linhas: str, cabecalho: str = CABECALHO):
    return csv_service.ler(db, (cabecalho + "\n" + linhas).encode("utf-8"))


# --- leitura do arquivo -------------------------------------------------------
def test_linha_valida_e_aceita(db):
    leitura = _ler(db, "Oficial de Dia;05/01/2026;SOUZA;;QG")
    assert len(leitura.aceitas) == 1 and not leitura.recusadas
    assert leitura.aceitas[0].dia == date(2026, 1, 5)


def test_escala_de_uma_vaga_dispensa_a_coluna_posto(db):
    assert _ler(db, "Oficial de Dia;05/01/2026;LIMA;;").aceitas


def test_escala_de_varias_vagas_exige_o_posto(db):
    """Sem o posto não há onde pôr o militar — e adivinhar seria inventar."""
    recusada = _ler(db, "Guarda;05/01/2026;LIMA;;").recusadas[0]
    assert "posto" in recusada.erro.lower()


def test_posto_pelo_rotulo_ou_pela_ordem(db):
    por_rotulo = _ler(db, "Guarda;05/01/2026;LIMA;Cabo da Guarda;")
    por_ordem = _ler(db, "Guarda;06/01/2026;LIMA;2;")
    assert por_rotulo.aceitas[0].posto_id == 3
    assert por_ordem.aceitas[0].posto_id == 3


def test_escala_desconhecida_e_recusada(db):
    r = _ler(db, "Escala Que Não Existe;05/01/2026;LIMA;;").recusadas[0]
    assert "não existe" in r.erro


def test_militar_desconhecido_e_recusado(db):
    r = _ler(db, "Oficial de Dia;05/01/2026;FULANO;;").recusadas[0]
    assert "não encontrado" in r.erro


def test_homonimo_sem_om_e_ambiguidade_nao_palpite(db):
    """SOUZA existe no QG e no B Ap: escolher um seria inventar histórico."""
    r = _ler(db, "Oficial de Dia;05/01/2026;SOUZA;;").recusadas[0]
    assert "mais de uma OM" in r.erro and "QG" in r.erro and "B Ap" in r.erro


def test_homonimo_com_om_resolve(db):
    assert _ler(db, "Oficial de Dia;05/01/2026;SOUZA;;B Ap").aceitas[0].militar_id == 3


def test_data_ilegivel_e_recusada(db):
    r = _ler(db, "Oficial de Dia;32/13/2026;LIMA;;").recusadas[0]
    assert "ilegível" in r.erro


def test_aceita_data_iso_alem_de_dd_mm_aaaa(db):
    assert _ler(db, "Oficial de Dia;2026-01-05;LIMA;;").aceitas[0].dia == date(2026, 1, 5)


def test_repetida_no_proprio_arquivo_e_recusada(db):
    leitura = _ler(db, "Oficial de Dia;05/01/2026;LIMA;;\n"
                       "Oficial de Dia;05/01/2026;SOUZA;;QG")
    assert len(leitura.aceitas) == 1
    assert "linha 2" in leitura.recusadas[0].erro


def test_dia_ja_gravado_no_banco_e_recusado(db):
    inicio = datetime.combine(date(2026, 1, 5), time(8, 0))
    db.add(Servico(escala_id=1, posto_id=1, militar_id=1, dia=date(2026, 1, 5),
                   cor=Cor.PRETA, inicio_dt=inicio,
                   termino_dt=datetime.combine(date(2026, 1, 6), time(8, 0))))
    db.commit()
    r = _ler(db, "Oficial de Dia;05/01/2026;LIMA;;").recusadas[0]
    assert "já existe" in r.erro.lower()


def test_nao_participante_entra_com_ressalva(db):
    """Fato consumado: serviu, ainda que hoje não participe (regra 7.6).

    Recusar impediria de carregar exatamente o histórico que se quer registrar.
    """
    leitura = _ler(db, "Oficial de Dia;05/01/2026;SOUZA;;B Ap")
    linha = leitura.aceitas[0]
    assert "participante" in " ".join(linha.avisos)


def test_cabecalho_incompleto_derruba_o_arquivo_inteiro(db):
    leitura = csv_service.ler(db, b"escala;data\nOficial de Dia;05/01/2026")
    assert leitura.erro_geral and "militar" in leitura.erro_geral


def test_arquivo_vazio(db):
    assert csv_service.ler(db, b"").erro_geral


def test_le_o_csv_do_excel_pt_br(db):
    """`;` e cp1252 — sem isto a primeira importação real morre num 'Ã§'."""
    conteudo = (CABECALHO + "\nOficial de Dia;05/01/2026;LIMA;;QG").encode("cp1252")
    assert csv_service.ler(db, conteudo).aceitas


def test_le_tambem_separado_por_virgula(db):
    conteudo = b"escala,data,militar,posto,om\nOficial de Dia,05/01/2026,LIMA,,QG"
    assert csv_service.ler(db, conteudo).aceitas


def test_cabecalho_com_acento_e_caixa_diferentes(db):
    conteudo = (b"ESCALA;DATA;MILITAR;POSTO;OM\n"
                b"oficial de dia;05/01/2026;lima;;qg")
    assert csv_service.ler(db, conteudo).aceitas


def test_linha_em_branco_e_ignorada(db):
    leitura = _ler(db, "Oficial de Dia;05/01/2026;LIMA;;\n;;;;\n")
    assert len(leitura.linhas) == 1


# --- gravação -----------------------------------------------------------------
def test_aplicar_grava_com_a_janela_da_escala(db):
    leitura = _ler(db, "Oficial de Dia;05/01/2026;LIMA;;")
    assert csv_service.aplicar(db, leitura) == 1
    s = db.scalar(select(Servico))
    assert s.inicio_dt == datetime(2026, 1, 5, 8, 0)
    assert s.termino_dt == datetime(2026, 1, 6, 8, 0)


def test_a_cor_vem_do_calendario_e_nao_do_arquivo(db):
    """Cor é consequência da data (regra 5): 10/01/2026 é sábado."""
    csv_service.aplicar(db, _ler(db, "Oficial de Dia;10/01/2026;LIMA;;"))
    assert db.scalar(select(Servico)).cor is Cor.VERMELHA


def test_feriado_da_om_torna_o_dia_vermelho_na_importacao(db):
    db.add(Feriado(data=date(2026, 1, 5), nome="Aniversário da OM", nacional=False))
    db.commit()
    csv_service.aplicar(db, _ler(db, "Oficial de Dia;05/01/2026;LIMA;;"))
    assert db.scalar(select(Servico)).cor is Cor.VERMELHA


def test_o_importado_alimenta_a_fila(db):
    """É o motivo de tudo: sem o histórico, todo mundo empata em 'nunca serviu'."""
    from app.services import mapeamento
    csv_service.aplicar(db, _ler(db, "Oficial de Dia;05/01/2026;LIMA;;"))
    db.flush()
    parts = {p.militar.nome_guerra: p
             for p in mapeamento.participacoes_da_escala(db, 1, date(2026, 2, 1))}
    assert parts["LIMA"].ultima_preta == date(2026, 1, 5)
    assert parts["SOUZA"].ultima_preta is None


# --- as duas etapas na tela ---------------------------------------------------
def test_conferir_nao_grava(client, db):
    r = client.post("/gestao/importar", files={
        "arquivo": ("h.csv", (CABECALHO + "\nOficial de Dia;05/01/2026;LIMA;;").encode(),
                    "text/csv")})
    assert r.status_code == 200
    assert db.scalar(select(func.count()).select_from(Servico)) == 0


def test_confirmar_grava(client, db):
    conteudo = CABECALHO + "\nOficial de Dia;05/01/2026;LIMA;;"
    r = client.post("/gestao/importar/confirmar", data={"conteudo": conteudo},
                    follow_redirects=False)
    assert r.status_code == 303 and "n=1" in r.headers["location"]
    assert db.scalar(select(func.count()).select_from(Servico)) == 1


def test_confirmar_sem_linha_aceita_nao_grava_nada(client, db):
    r = client.post("/gestao/importar/confirmar",
                    data={"conteudo": CABECALHO + "\nInexistente;05/01/2026;X;;"})
    assert r.status_code == 400
    assert db.scalar(select(func.count()).select_from(Servico)) == 0


def test_tela_lista_o_motivo_de_cada_recusa(client):
    r = client.post("/gestao/importar", files={
        "arquivo": ("h.csv", (CABECALHO + "\nInexistente;05/01/2026;LIMA;;").encode(),
                    "text/csv")})
    assert "não existe" in r.text


def test_sem_arquivo_vira_mensagem_e_nao_erro_de_sistema(client):
    r = client.post("/gestao/importar", files={})
    assert r.status_code == 400 and "arquivo" in r.text.lower()


def test_importacao_fica_na_auditoria(client, db):
    from app.models.gestao import Auditoria
    client.post("/gestao/importar/confirmar", follow_redirects=False,
                data={"conteudo": CABECALHO + "\nOficial de Dia;05/01/2026;LIMA;;"})
    registro = db.scalar(select(Auditoria).where(Auditoria.entidade == "servico"))
    assert registro is not None and registro.dados_depois["origem"] == "importação CSV"


def test_modelo_sai_com_os_nomes_reais(client):
    r = client.get("/gestao/importar/modelo.csv")
    assert r.status_code == 200
    texto = r.content.decode("utf-8-sig")
    assert texto.splitlines()[0] == ";".join(csv_service.COLUNAS)
    assert "Oficial de Dia" in texto or "Guarda" in texto


def test_a_tela_exige_login(db):
    """Importar é gestão (regra 11), não consulta aberta."""
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as anonimo:
        r = anonimo.get("/gestao/importar", follow_redirects=False)
        assert r.status_code == 303 and "login" in r.headers["location"]
    app.dependency_overrides.clear()
