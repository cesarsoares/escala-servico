"""Testes da importação da ficha individual em PDF (regra 3.2).

O importador PRÉ-PREENCHE o cadastro e nunca grava — o operador confere e salva
pelo caminho normal. Os PDFs são montados aqui (tests/pdf_sintetico.py) para não
depender de fichas reais, que carregam dados pessoais.

Os dois formatos exercitados reproduzem as armadilhas encontradas nas fichas
reais: no SiCaPEx os rótulos vêm truncados e a tabela de afastamentos (cheia de
datas) fica logo abaixo das datas de praça; no SCGPE o texto é uma grade de
colunas com marca d'água diagonal, e um campo vazio no meio da linha desloca os
valores se a leitura for por linha em vez de por coluna.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.seeds import seed_circulos, seed_postos_graduacao
from app.seeds.usuario import criar_ou_atualizar_gestor
from app.services import ficha as leitor
from app.services import importacao
from tests.pdf_sintetico import colunas, linhas, pdf


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
    sessao.add(OrganizacaoMilitar(id=1, nome="Comando Militar do Sul", sigla="Cmdo CMS"))
    criar_ou_atualizar_gestor(sessao, "brigada", "senha-boa-123", "Sgt Brigada")
    sessao.commit()
    yield sessao
    sessao.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def auth(client):
    token = client.post(
        "/api/auth/login", data={"username": "brigada", "password": "senha-boa-123"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def cookie_gestor(client):
    client.post("/gestao/login", data={"username": "brigada", "password": "senha-boa-123"},
                follow_redirects=False)
    return client


# --- fichas sintéticas --------------------------------------------------------
def ficha_sicapex(**over) -> bytes:
    """Formato antigo: rótulos truncados na mesma linha do valor."""
    d = {"nome": "FULANO DE TAL DA SILVA", "posto": "3o Sgt", "guerra": "FULANO",
         "idt": "0311111111", "cpf": "605.126.360-87", "nasc": "07/09/1972",
         "om": "Cmdo CMS - 023556"}
    d.update(over)
    p1 = linhas(40, 800, [
        "FICHA CADASTRO - SiCaPEx",
        "MINISTERIO DA DEFESA",
        "DADOS PESSOAIS",
        f"Nome: {d['nome']}",
        f"CPF: {d['cpf']} Pis/Pasep: 12361564647 RA:",
        f"Dt Nasc: {d['nasc']} Idade: 53 anos Nacionalidad Brasileiro(a)",
        "DADOS FUNCIONAIS",
        f"Posto/Grad: {d['posto']} Nome {d['guerra']} Dt Turma: 25/11/1995",
        "Dt Ultima 01/12/2023 QAS/QMS/QM 6400 - QAO - TOPOGRAFO",
        f"Cmdo: CMS RM: 3a RM OM/CODOM: {d['om']} Dt Inicio 11/09/2023",
        "Documentos Funcionais",
        f"Idt {d['idt']} Prec-CP: 34-2376101 CP: 92502-4",
    ])
    p2 = linhas(40, 800, [
        "Datas de Praça",
        "Dt Praça Dt Desligamento Tipo de Força Documento",
        "01/03/2018 15/06/2018 Normal EB BI Nr 38, de 16/03/2018 do(a) 8o B Log",
        "18/06/2018 17/06/2022 Normal EB BAR Nr 044, de 04/07/2018 do(a) 13o GAC",
        "Endereços",
        "AFASTAMENTOS",
        "Dispensa como recompensa 5 04/08/2025 08/08/2025 BI Cmdo CMS Nr 63",
        "PROMOÇÕES",
        "Tipo Promoção Posto/Grad Dt Promoção Documento",
        "Antiguidade 1o Ten 01/12/2023 DOU/1 dez 23",
        "Merecimento 3o Sgt 25/11/1995 DOU Nr 104-E",
        "Merecimento Cb 01/04/1992",
        "QFE",
    ])
    return pdf([p1, p2])


def ficha_scgpe(*, data_turma: str = "25/11/1995", praca: str = "04/02/1991",
                rotulo_cnh: str = "Nº CNH:") -> bytes:
    """Formato novo: grade de colunas + marca d'água diagonal.

    `data_turma` vazia reproduz a ficha em que a leitura por linha faz a data de
    praça escorregar para a coluna anterior; `rotulo_cnh` permite simular uma
    variação de grafia num rótulo da linha.
    """
    p1 = [
        *linhas(40, 800, ["Informação de Pessoal - Acesso Restrito",
                          "FICHA INDIVIDUAL 1o Ten CESAR",
                          "Dados Individuais"]),
        *colunas(740, [(40, "Nome Completo:"), (200, "CPF"),
                       (330, "Identidade Civil:"), (460, rotulo_cnh)]),
        *colunas(726, [(40, "BELTRANO DOS SANTOS"), (200, "60512636087"),
                       (330, "4053153534"), (460, "N/A")]),
        *colunas(712, [(40, "BELTRANO")]),          # nome longo quebra em duas linhas
        *colunas(690, [(40, "Categoria CNH:"), (200, "Sexo:"),
                       (330, "Estado Civil:"), (460, "Data Nascimento:")]),
        *colunas(676, [(40, "N/A"), (200, "Masculino"), (330, "Casado"), (460, "07/09/1972")]),
        *colunas(640, [(40, "Posto Graduação:"), (150, "QAS/QMS:"), (260, "Nome Guerra:"),
                       (370, "Situação Atual:"), (470, "Identidade Militar:")]),
        *colunas(626, [(40, "2º Sgt"), (150, "QAO"), (260, "BELTRANO"),
                       (370, "Ativo"), (470, "0317884740")]),
        *colunas(600, [(40, "PREC/CP:"), (140, "CP:"), (230, "Registro de Alistamento"),
                       (380, "Data Turma:"), (470, "Última Data Praça:")]),
        *colunas(586, [(40, "342376101"), (140, "925024")]
                 + ([(380, data_turma)] if data_turma else []) + [(470, praca)]),
        *colunas(560, [(40, "Comando:"), (160, "Região Militar:"),
                       (300, "OM Atual"), (450, "Local da OM:")]),
        *colunas(546, [(40, "Comando Militar do Sul"), (160, "3a REGIAO MILITAR"),
                       (300, "Cmdo CMS"), (450, "RUA DOS ANDRADAS")]),
        (300, 300, "Usuario: 113932 | OM/CODOM: 023556", True),   # marca d'água
    ]
    p2 = linhas(40, 800, [
        "Data Praça",
        "Data Praça Data Desligamento OM Força",
        f"{praca} N/A Cia Cmdo 6a DE EB",
        "Habilitações Cursos/Estágios",
        "Promoções Sucessivas",
        "Tipo da Promoção Posto/Graduação Data da Promoção Documento de Promoção",
        "Antiguidade 1º Sgt 01/12/2010 DOU/01 Dez 10",
        "Merecimento 2º Sgt 01/06/2002 DOU Nr 104-E",
        "Merecimento 3º Sgt 25/11/1995 N/A",
        "Punições Disciplinares",
    ])
    return pdf([p1, p2])


# --- leitor: formato SiCaPEx --------------------------------------------------
def test_sicapex_le_todos_os_campos():
    f = leitor.extrair(ficha_sicapex())
    assert f.formato == "sicapex"
    assert f.nome_completo == "FULANO DE TAL DA SILVA"
    assert f.nome_guerra == "FULANO"
    assert f.posto_texto == "3o Sgt"
    assert f.identidade == "0311111111"
    assert f.cpf == "60512636087"          # só dígitos, sem a máscara
    assert f.data_nascimento.isoformat() == "1972-09-07"
    assert f.om_texto.startswith("Cmdo CMS")


def test_sicapex_data_praca_e_a_ultima_e_nao_vaza_para_afastamentos():
    """Duas passagens: vale a mais recente. E a tabela de afastamentos logo
    abaixo (04/08/2025) não pode ser confundida com data de praça."""
    f = leitor.extrair(ficha_sicapex())
    assert f.data_praca.isoformat() == "2018-06-18"


def test_promocao_casa_pelo_posto_atual_e_nao_pela_primeira_linha():
    """A tabela abre pela promoção mais recente (1o Ten); o militar da ficha é
    3o Sgt, então vale a linha dele."""
    f = leitor.extrair(ficha_sicapex())
    assert f.data_promocao.isoformat() == "1995-11-25"
    assert not any("primeira linha" in a for a in f.avisos)


def test_promocao_sem_linha_do_posto_atual_avisa():
    f = leitor.extrair(ficha_sicapex(posto="Cap"))
    assert f.data_promocao.isoformat() == "2023-12-01"   # cai na primeira linha
    assert any("não confere com o posto atual" in a for a in f.avisos)


# --- leitor: formato SCGPE ----------------------------------------------------
def test_scgpe_le_a_grade_e_ignora_a_marca_dagua():
    f = leitor.extrair(ficha_scgpe())
    assert f.formato == "scgpe"
    assert f.nome_completo == "BELTRANO DOS SANTOS BELTRANO"   # valor de 2 linhas
    assert f.nome_guerra == "BELTRANO"
    assert f.posto_texto == "2º Sgt"
    assert f.identidade == "0317884740"
    assert f.data_nascimento.isoformat() == "1972-09-07"
    assert f.data_promocao.isoformat() == "2002-06-01"
    # a marca d'água diagonal não pode contaminar nenhum campo
    assert all("113932" not in (v or "") for v in
               (f.nome_completo, f.nome_guerra, f.identidade, f.cpf))


def test_scgpe_campo_vazio_no_meio_nao_desloca_a_coluna():
    """Com `Data Turma` vazia, ler por linha traria a data de praça na coluna
    errada; por coordenada, cada valor fica na sua."""
    f = leitor.extrair(ficha_scgpe(data_turma="", praca="01/02/2019"))
    assert f.data_praca.isoformat() == "2019-02-01"


def test_scgpe_tolera_rotulo_com_grafia_diferente():
    """Um rótulo que não casa fica sem valor, mas não derruba a linha inteira."""
    f = leitor.extrair(ficha_scgpe(rotulo_cnh="No. CNH:"))
    assert f.nome_completo == "BELTRANO DOS SANTOS BELTRANO"
    assert f.cpf == "60512636087"


def test_pdf_que_nao_e_ficha_e_rejeitado():
    outro = pdf([linhas(40, 800, ["RELATORIO QUALQUER", "nada a ver"])])
    with pytest.raises(leitor.FichaInvalida):
        leitor.extrair(outro)


def test_arquivo_corrompido_e_rejeitado():
    with pytest.raises(leitor.FichaInvalida):
        leitor.extrair(b"isto nao e um pdf")


# --- reconciliação com as tabelas de referência -------------------------------
def test_rascunho_resolve_posto_e_om(db):
    valores, avisos = importacao.rascunho(db, leitor.extrair(ficha_sicapex()))
    esperado = db.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "3º Sgt"))
    assert valores["posto_graduacao_id"] == esperado   # '3o Sgt' sem acento casa
    assert valores["om_id"] == 1
    assert valores["numero_antiguidade"] is None       # nunca vem da ficha (regra 9.5)
    assert any("antiguidade" in a for a in avisos)


def test_om_desconhecida_fica_em_branco_com_aviso(db):
    """Nunca chutar OM: o SCGPE traz o nome por extenso, que pode não casar."""
    valores, avisos = importacao.rascunho(
        db, leitor.extrair(ficha_sicapex(om="Batalhao Fantasma - 999999")))
    assert valores["om_id"] is None
    assert any("não corresponde a nenhuma cadastrada" in a for a in avisos)


def test_avisa_quando_identidade_ja_esta_cadastrada(db):
    db.add(Militar(nome_guerra="Ciclano", nome_completo="Ciclano de Tal",
                   identidade="0311111111", posto_graduacao_id=1, om_id=1))
    db.commit()
    _, avisos = importacao.rascunho(db, leitor.extrair(ficha_sicapex()))
    assert any("já pertence a Ciclano" in a for a in avisos)


def test_avisa_homonimo_do_efetivo_carregado_sem_identidade(db):
    """Quem veio da planilha não tem identidade nem CPF: sem esse aviso o
    operador criaria um segundo cadastro da mesma pessoa."""
    db.add(Militar(nome_guerra="Fulano", nome_completo="Fulano de Tal da Silva",
                   posto_graduacao_id=1, om_id=1))
    db.commit()
    _, avisos = importacao.rascunho(db, leitor.extrair(ficha_sicapex()))
    assert any("Já existe militar com este nome de guerra" in a for a in avisos)


# --- API ----------------------------------------------------------------------
def test_importar_sem_token_401(client):
    r = client.post("/api/militares/importar-ficha",
                    files={"arquivo": ("f.pdf", ficha_sicapex(), "application/pdf")})
    assert r.status_code == 401


def test_importar_devolve_rascunho_sem_gravar(client, auth, db):
    antes = db.scalar(select(func.count()).select_from(Militar))
    r = client.post("/api/militares/importar-ficha", headers=auth,
                    files={"arquivo": ("f.pdf", ficha_sicapex(), "application/pdf")})
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["formato"] == "sicapex"
    assert corpo["militar"]["nome_guerra"] == "FULANO"
    assert corpo["militar"]["data_praca"] == "2018-06-18"
    assert db.scalar(select(func.count()).select_from(Militar)) == antes   # nada gravado


def test_importar_pdf_invalido_422(client, auth):
    r = client.post("/api/militares/importar-ficha", headers=auth,
                    files={"arquivo": ("x.pdf", b"nao e pdf", "application/pdf")})
    assert r.status_code == 422


# --- tela de gestão -----------------------------------------------------------
def test_tela_importar_preenche_o_formulario(cookie_gestor):
    r = cookie_gestor.post("/gestao/militares/importar",
                           files={"arquivo": ("f.pdf", ficha_sicapex(), "application/pdf")})
    assert r.status_code == 200
    assert "FULANO DE TAL DA SILVA" in r.text     # value do campo já preenchido
    assert "2018-06-18" in r.text
    assert "nada foi gravado ainda" in r.text


def test_tela_importar_na_edicao_preserva_o_que_a_ficha_nao_traz(cookie_gestor, db):
    """Caso real da carga da planilha: o militar já existe com nome/posto/OM e a
    ficha completa identidade e datas, sem apagar o que não vem nela."""
    m = Militar(nome_guerra="Antigo", nome_completo="Nome Antigo",
                posto_graduacao_id=1, om_id=1, numero_antiguidade=42)
    db.add(m)
    db.commit()
    r = cookie_gestor.post(f"/gestao/militares/{m.id}/importar",
                           files={"arquivo": ("f.pdf", ficha_sicapex(), "application/pdf")})
    assert r.status_code == 200
    assert "0311111111" in r.text      # identidade veio da ficha
    assert 'value="42"' in r.text      # antiguidade preexistente preservada
    assert db.get(Militar, m.id).identidade is None    # e nada foi gravado
