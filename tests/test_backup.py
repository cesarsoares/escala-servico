"""Backup, restauração e exportação (regra 13.3 — instalação local por OM).

O que estes testes protegem, em ordem de gravidade:

  1. **restaurar não pode destruir sem rede** — o banco anterior fica guardado,
     e um arquivo que não é backup deste sistema é recusado com motivo;
  2. **o backup tem de conter o que foi gravado até o último instante** — com
     WAL, uma cópia ingênua do arquivo pode não conter;
  3. **exportação não é backup**, e o `servicos.csv` que sai daqui tem de voltar
     por `/gestao/importar` sem ninguém reescrever cabeçalho.

Os testes de arquivo usam banco EM DISCO (tmp_path), não `:memory:`: é o
arquivo que está sendo testado.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.calendario import Feriado
from app.models.escala import Escala, Participacao, Posto
from app.models.gestao import Auditoria, Usuario
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import (
    Configuracao, OrganizacaoMilitar, PostoGraduacao, TipoImpedimento,
)
from app.models.servico import Permuta, Servico
from app.seeds import seed_circulos, seed_postos_graduacao
from app.services import backup as bkp
from app.services import configuracao as cfg
from app.services import exportacao, importacao_csv
from app.domain.models import Cor


# --- fixtures -----------------------------------------------------------------
def _povoar(db: Session) -> None:
    """Uma instalação pequena, mas com um exemplar de cada coisa exportável."""
    seed_circulos(db)
    seed_postos_graduacao(db)
    db.add(OrganizacaoMilitar(id=1, nome="1º Batalhão de Infantaria", sigla="1º BI",
                              propria=True))
    db.add(OrganizacaoMilitar(id=2, nome="2º Batalhão Logístico", sigla="2º BLog"))
    db.flush()
    sgt = db.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "2º Sgt"))
    cb = db.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "Cb"))

    db.add(Militar(id=1, nome_guerra="SOUZA", nome_completo="Ana de Souza",
                   posto_graduacao_id=sgt, om_id=1, identidade="0123456789",
                   cpf="11122233344", data_promocao=date(2023, 3, 25)))
    db.add(Militar(id=2, nome_guerra="LIMA", nome_completo="Bruno Lima",
                   posto_graduacao_id=cb, om_id=2, numero_antiguidade=42))
    db.add(Escala(id=1, nome="Oficial de Dia", inicio_servico=time(8, 0),
                  duracao_horas=24))
    db.add(Posto(id=1, escala_id=1, ordem=1, rotulo="Oficial de Dia"))
    db.flush()
    db.add(Participacao(militar_id=1, escala_id=1))
    db.add(Participacao(militar_id=2, escala_id=1, serve_preta=False))
    db.add(TipoImpedimento(id=1, nome="Férias"))
    db.add(Feriado(data=date(2026, 9, 7), nome="Independência", nacional=True))
    db.flush()
    db.add(Impedimento(militar_id=2, tipo_impedimento_id=1,
                       inicio=date(2026, 8, 1), fim=date(2026, 8, 20)))
    inicio = datetime(2026, 7, 20, 8, 0)
    db.add(Servico(id=1, escala_id=1, posto_id=1, militar_id=1, dia=date(2026, 7, 20),
                   cor=Cor.PRETA, inicio_dt=inicio, termino_dt=inicio + timedelta(hours=24)))
    db.add(Usuario(id=1, login="brigada", senha_hash="x", nome="Sgt Brigada"))
    db.flush()
    db.add(Permuta(servico_id=1, militar_substituto_id=2, autorizado_por=1,
                   observacao="troca combinada"))
    db.commit()


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = Session(engine)
    _povoar(s)
    yield s
    s.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def logado(client, db):
    u = db.scalar(select(Usuario).where(Usuario.login == "brigada"))
    from app.web.gestao import gestor_web
    app.dependency_overrides[gestor_web] = lambda: u
    yield client
    app.dependency_overrides.pop(gestor_web, None)


@pytest.fixture()
def banco_em_arquivo(tmp_path) -> Path:
    """Instalação de verdade: banco em disco, com a marca de versão do Alembic.

    A revisão gravada é a que o CÓDIGO espera — assim o teste de restauração não
    dispara migração, que é caminho separado.
    """
    caminho = tmp_path / "escala.sqlite3"
    engine = create_engine(f"sqlite+pysqlite:///{caminho.as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        _povoar(s)
    engine.dispose()
    _marcar_versao(caminho)
    return caminho


def _marcar_versao(caminho: Path) -> None:
    """Põe a marca do Alembic e CHECKPOINT do WAL.

    Sem fechar a conexão, o que foi escrito fica no arquivo `-wal` e o
    `.sqlite3` sozinho sai sem a tabela — o mesmo tropeço que a cópia ingênua
    de backup produziria em produção.
    """
    head, _ = bkp._revisoes()
    con = sqlite3.connect(caminho)
    try:
        con.execute("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)")
        con.execute("INSERT INTO alembic_version VALUES (?)", (head,))
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()


# --- 1. baixar ----------------------------------------------------------------
def test_a_copia_e_um_banco_sqlite_com_os_dados(db):
    """O que se baixa tem de ser um banco abrível, com o efetivo dentro."""
    conteudo = bkp.copia(db)
    assert conteudo[:16] == bkp.MAGIC
    with sqlite3.connect(":memory:") as _:
        pass
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as tmp:
        arq = Path(tmp) / "b.sqlite3"
        arq.write_bytes(conteudo)
        con = sqlite3.connect(arq)
        assert con.execute("SELECT COUNT(*) FROM militar").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM servico").fetchone()[0] == 1
        con.close()


def test_a_copia_leva_o_que_acabou_de_ser_gravado(db):
    """Com WAL, copiar o .sqlite3 na mão pode não levar a última gravação.

    É o motivo de o backup usar a API do SQLite e não `shutil.copy`: o gestor
    fecha o mês e baixa em seguida, e o mês tem de estar lá.
    """
    inicio = datetime(2026, 7, 21, 8, 0)
    db.add(Servico(escala_id=1, posto_id=1, militar_id=2, dia=date(2026, 7, 21),
                   cor=Cor.PRETA, inicio_dt=inicio, termino_dt=inicio + timedelta(hours=24)))
    db.commit()

    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as tmp:
        arq = Path(tmp) / "b.sqlite3"
        arq.write_bytes(bkp.copia(db))
        con = sqlite3.connect(arq)
        assert con.execute("SELECT COUNT(*) FROM servico").fetchone()[0] == 2
        con.close()


def test_o_nome_do_arquivo_traz_a_om_e_o_instante():
    """Fora daqui, o nome é a única coisa que diz de quando é o arquivo."""
    nome = bkp.nome_sugerido("1º BI", datetime(2026, 7, 28, 14, 32))
    assert nome == "escala-1BI-2026-07-28-1432.sqlite3"
    assert bkp.nome_sugerido("///").startswith("escala-OM-")


def test_postgres_nao_oferece_backup_por_arquivo():
    """Botão que produz arquivo inútil é pior que ausência de botão."""
    assert bkp.caminho_do_banco("postgresql+psycopg://u:p@h/escala") is None
    assert bkp.eh_arquivo("postgresql+psycopg://u:p@h/escala") is False
    assert bkp.caminho_do_banco("sqlite+pysqlite:///:memory:") is None
    assert bkp.eh_arquivo("sqlite:////dados/escala.sqlite3") is True


# --- 2. inspecionar (a conferência antes de restaurar) ------------------------
def test_o_retrato_diz_o_que_ha_no_arquivo(banco_em_arquivo):
    r = bkp.inspecionar(banco_em_arquivo)
    assert r.om == "1º BI"
    assert (r.militares, r.escalas, r.servicos, r.impedimentos) == (2, 1, 1, 1)
    assert r.ultimo_servico == date(2026, 7, 20)
    assert [g.login for g in r.gestores] == ["brigada"]
    assert r.precisa_migrar is False


def test_arquivo_que_nao_e_banco_e_recusado_com_motivo(tmp_path):
    """Planilha ou PDF com o nome trocado é o engano provável."""
    falso = tmp_path / "escala.sqlite3"
    falso.write_bytes(b"%PDF-1.4 nada a ver")
    with pytest.raises(bkp.ErroBackup, match="não é um banco SQLite"):
        bkp.inspecionar(falso)


def test_banco_sem_marca_de_versao_e_recusado(tmp_path):
    """Um SQLite qualquer não é backup DESTE sistema."""
    outro = tmp_path / "outro.sqlite3"
    con = sqlite3.connect(outro)
    con.execute("CREATE TABLE qualquer (a int)")
    con.commit()
    con.close()
    with pytest.raises(bkp.ErroBackup, match="marca de versão"):
        bkp.inspecionar(outro)


def test_backup_de_versao_mais_nova_e_recusado(banco_em_arquivo):
    """O Alembic sobe de versão, não desce: restaurar seria quebrar o schema."""
    con = sqlite3.connect(banco_em_arquivo)
    con.execute("UPDATE alembic_version SET version_num = 'aindanaoexiste'")
    con.commit()
    con.close()
    with pytest.raises(bkp.ErroBackup, match="MAIS NOVA"):
        bkp.inspecionar(banco_em_arquivo)


def test_arquivo_vazio_e_recusado(tmp_path):
    vazio = tmp_path / "vazio.sqlite3"
    vazio.write_bytes(b"")
    with pytest.raises(bkp.ErroBackup, match="vazio"):
        bkp.inspecionar(vazio)


def test_o_retrato_sabe_se_o_gestor_logado_existe_no_backup(banco_em_arquivo):
    """É o aviso da tela: restaurar sem o próprio login é perder o acesso."""
    r = bkp.inspecionar(banco_em_arquivo)
    assert r.tem_gestor("brigada") is True
    assert r.tem_gestor("BRIGADA") is True        # login não diferencia caixa
    assert r.tem_gestor("outro") is False


def test_gestor_inativo_no_backup_nao_conta_como_acesso(banco_em_arquivo):
    con = sqlite3.connect(banco_em_arquivo)
    con.execute("UPDATE usuario SET ativo = 0")
    con.commit()
    con.close()
    assert bkp.inspecionar(banco_em_arquivo).tem_gestor("brigada") is False


# --- 3. restaurar -------------------------------------------------------------
@pytest.fixture()
def instalacao_em_arquivo(tmp_path, monkeypatch, banco_em_arquivo):
    """Aponta a aplicação para um banco em disco, como numa OM de verdade."""
    from app import config, database

    url = f"sqlite+pysqlite:///{banco_em_arquivo.as_posix()}"
    monkeypatch.setattr(config.settings, "database_url", url)
    engine = create_engine(url)
    monkeypatch.setattr(database, "engine", engine)
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(database, "SessionLocal",
                        sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))
    yield banco_em_arquivo
    engine.dispose()


def _outro_backup(tmp_path, sigla: str, militares: int) -> bytes:
    """Um backup de OUTRA instalação, para ver se a troca de fato aconteceu."""
    caminho = tmp_path / f"{sigla}.sqlite3"
    engine = create_engine(f"sqlite+pysqlite:///{caminho.as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed_circulos(s)
        seed_postos_graduacao(s)
        s.add(OrganizacaoMilitar(nome=f"OM {sigla}", sigla=sigla, propria=True))
        s.flush()
        pg = s.scalar(select(PostoGraduacao.id).where(PostoGraduacao.sigla == "Cb"))
        om = s.scalar(select(OrganizacaoMilitar.id))
        for i in range(militares):
            s.add(Militar(nome_guerra=f"M{i}", nome_completo=f"Militar {i}",
                          posto_graduacao_id=pg, om_id=om))
        s.add(Usuario(login="outro", senha_hash="x", nome="Outro Gestor"))
        s.commit()
    engine.dispose()
    _marcar_versao(caminho)
    return caminho.read_bytes()


def test_restaurar_troca_o_banco_e_guarda_o_anterior(instalacao_em_arquivo, tmp_path):
    """O ponto inteiro: os dados novos entram e os antigos NÃO somem do disco."""
    token = bkp.guardar_envio(_outro_backup(tmp_path, "9BI", 5))
    feito = bkp.restaurar(token)

    assert feito.retrato.om == "9BI"
    con = sqlite3.connect(instalacao_em_arquivo)
    assert con.execute("SELECT COUNT(*) FROM militar").fetchone()[0] == 5
    con.close()

    guardado = feito.copia_de_seguranca
    assert guardado is not None and guardado.exists()
    assert "antes-da-restauracao" in guardado.name
    con = sqlite3.connect(guardado)
    assert con.execute("SELECT sigla FROM organizacao_militar WHERE propria = 1"
                       ).fetchone()[0] == "1º BI"
    con.close()


def test_o_envio_conferido_some_depois_de_restaurado(instalacao_em_arquivo, tmp_path):
    """Banco inteiro largado no disco é lixo — e lixo com dado pessoal dentro."""
    token = bkp.guardar_envio(_outro_backup(tmp_path, "9BI", 2))
    caminho = bkp.caminho_envio(token)
    assert caminho.exists()
    bkp.restaurar(token)
    assert not caminho.exists()


def test_envio_expirado_recusa_em_vez_de_restaurar_lixo(instalacao_em_arquivo, tmp_path):
    token = bkp.guardar_envio(_outro_backup(tmp_path, "9BI", 2))
    bkp.caminho_envio(token).unlink()
    with pytest.raises(bkp.ErroBackup, match="não está mais disponível"):
        bkp.restaurar(token)


def test_token_com_travessia_de_caminho_nao_alcanca_nada(instalacao_em_arquivo):
    """`../../escala.sqlite3` não pode virar caminho — o token é peneirado."""
    alvo = bkp.caminho_envio("../../escala")
    assert alvo.name == "escala.sqlite3"
    assert alvo.parent.name == bkp.PASTA_ENVIOS
    with pytest.raises(bkp.ErroBackup):
        bkp.caminho_envio("../..")


def test_limpar_envios_apaga_so_o_que_passou_da_validade(instalacao_em_arquivo, tmp_path):
    novo = bkp.caminho_envio(bkp.guardar_envio(_outro_backup(tmp_path, "9BI", 1)))
    velho = novo.with_name("velho.sqlite3")
    velho.write_bytes(b"x")
    antigo = (datetime.now() - bkp.VALIDADE_ENVIO - timedelta(minutes=5)).timestamp()
    import os
    os.utime(velho, (antigo, antigo))

    assert bkp.limpar_envios() == 1
    assert not velho.exists()
    assert novo.exists()


def test_restaurar_recusa_arquivo_que_nao_e_backup(instalacao_em_arquivo):
    """A releitura da etapa 2 é o que impede confirmar um envio adulterado."""
    token = bkp.guardar_envio(b"nem de longe um banco")
    with pytest.raises(bkp.ErroBackup, match="não é um banco SQLite"):
        bkp.restaurar(token)
    con = sqlite3.connect(instalacao_em_arquivo)
    assert con.execute("SELECT COUNT(*) FROM militar").fetchone()[0] == 2
    con.close()


# --- 4. exportação ------------------------------------------------------------
def test_o_pacote_traz_um_arquivo_por_assunto(db):
    nomes = set(exportacao.arquivos(db))
    assert nomes == {
        "LEIA-ME.txt", "militares.csv", "escalas.csv", "postos.csv",
        "participantes.csv", "servicos.csv", "impedimentos.csv", "permutas.csv",
        "calendario.csv", "auditoria.csv",
    }


def test_o_leia_me_diz_que_isto_nao_restaura(db):
    """É a confusão que custa caro no dia ruim."""
    texto = exportacao.arquivos(db)["LEIA-ME.txt"]
    assert "NÃO RESTAURAM" in texto
    assert "1º BI" in texto


def test_cpf_e_identidade_ficam_de_fora_por_padrao(db):
    sem = exportacao.militares(db)
    assert "11122233344" not in sem and "0123456789" not in sem
    assert "cpf" not in sem.splitlines()[0]

    com = exportacao.militares(db, incluir_pessoais=True)
    assert "11122233344" in com and "cpf" in com.splitlines()[0]
    assert "dados pessoais" in exportacao.arquivos(db, True)["LEIA-ME.txt"]


def test_o_historico_exportado_volta_pelo_importador(db):
    """O par que fecha: o que sai daqui entra em /gestao/importar sem edição.

    O serviço já gravado é recusado como duplicado — é o que se espera, e prova
    que o arquivo foi RECONHECIDO (escala, data, militar e posto casaram).
    """
    conteudo = exportacao.servicos(db)
    assert conteudo.splitlines()[0].split(";") == list(importacao_csv.COLUNAS)

    leitura = importacao_csv.ler(db, conteudo.encode("utf-8-sig"))
    assert leitura.erro_geral is None
    assert len(leitura.linhas) == 1
    assert leitura.linhas[0].erro == "Já existe serviço gravado neste posto e dia."


def test_o_historico_exportado_entra_numa_instalacao_nova(db, tmp_path):
    """O caso real: levar o passado para outra instalação com o mesmo efetivo."""
    conteudo = exportacao.servicos(db)
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as nova:
        _povoar(nova)
        nova.execute(Servico.__table__.delete())     # instalação sem histórico
        nova.commit()
        leitura = importacao_csv.ler(nova, conteudo.encode("utf-8-sig"))
        assert leitura.recusadas == []
        assert importacao_csv.aplicar(nova, leitura) == 1
    engine.dispose()


def test_a_permuta_exportada_diz_de_quem_e_a_folga(db):
    """A folga é do ESCALADO (regra 9) — a ordem das colunas tem de dizer isso."""
    texto = exportacao.permutas(db)
    cabecalho = texto.splitlines()[0].split(";")
    assert cabecalho.index("escalado_folga_e_dele") < cabecalho.index("substituto")
    assert "SOUZA;LIMA" in texto


def test_a_concorrencia_aparece_nos_dois_lados(db):
    """Simétrica no banco por um par ordenado (7.4.1); na planilha, nos dois."""
    from app.models.escala import EscalaConcorrente
    db.add(Escala(id=2, nome="Adjunto", inicio_servico=time(8, 0), duracao_horas=24))
    db.flush()
    db.add(EscalaConcorrente(escala_menor_id=1, escala_maior_id=2))
    db.commit()

    linhas = exportacao.escalas(db).splitlines()
    assert any(l.startswith("Adjunto;") and "Oficial de Dia" in l for l in linhas)
    assert any(l.startswith("Oficial de Dia;") and "Adjunto" in l for l in linhas)


def test_a_auditoria_sai_no_fuso_de_quem_le(db):
    """O banco grava em UTC; exportar o valor cru adiantaria 3h em silêncio."""
    quando = datetime(2026, 7, 28, 12, 0)        # UTC, como func.now() grava
    db.add(Auditoria(usuario_id=1, entidade="militar", entidade_id=1, acao="alterar",
                     criado_em=quando))
    db.commit()
    linha = exportacao.auditoria(db).splitlines()[1]
    esperado = quando.replace(tzinfo=__import__("datetime").timezone.utc
                              ).astimezone().strftime("%d/%m/%Y %H:%M")
    assert linha.startswith(esperado)


def test_o_zip_abre_e_os_csv_tem_bom(db):
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(exportacao.pacote(db))) as zf:
        assert zf.read("militares.csv").startswith(b"\xef\xbb\xbf")   # Excel pt-BR
        assert not zf.read("LEIA-ME.txt").startswith(b"\xef\xbb\xbf")


# --- 5. telas -----------------------------------------------------------------
def test_a_tela_de_backup_exige_login(client):
    r = client.get("/gestao/configuracao/backup", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/gestao/login"


def test_a_tela_mostra_as_tres_coisas(logado):
    corpo = logado.get("/gestao/configuracao/backup").text
    assert "Baixar backup" in corpo
    assert "Exportar dados em CSV" in corpo
    assert "Restaurar a partir de um backup" in corpo


def test_baixar_devolve_o_banco_e_anota_a_data(logado, db):
    r = logado.post("/gestao/configuracao/backup/baixar")
    assert r.status_code == 200
    assert r.content[:16] == bkp.MAGIC
    assert ".sqlite3" in r.headers["content-disposition"]

    assert cfg.valor(db, "ultimo_backup_em") != ""
    registro = db.scalars(select(Auditoria).where(Auditoria.entidade == "backup")).all()
    assert len(registro) == 1 and registro[0].acao == "criar"


def test_o_cartao_do_hub_cobra_quando_nunca_houve_backup(db):
    cartao = next(c for c in cfg.panorama(db) if c.chave == "backup")
    assert "nenhum backup" in cartao.pendencia

    cfg.definir(db, "ultimo_backup_em", datetime.now().isoformat(timespec="minutes"))
    db.commit()
    assert next(c for c in cfg.panorama(db) if c.chave == "backup").pendencia == ""


def test_o_cartao_cobra_de_novo_depois_de_um_mes(db):
    velho = datetime.now() - timedelta(days=cfg.DIAS_SEM_BACKUP + 1)
    cfg.definir(db, "ultimo_backup_em", velho.isoformat(timespec="minutes"))
    db.commit()
    cartao = next(c for c in cfg.panorama(db) if c.chave == "backup")
    assert "dias" in cartao.pendencia


def test_marca_de_backup_ilegivel_nao_derruba_o_hub(db):
    """Valor estragado no banco vira 'nunca houve', não 500 na tela toda."""
    db.add(Configuracao(chave="ultimo_backup_em", valor="ontem de tarde"))
    db.commit()
    cartao = next(c for c in cfg.panorama(db) if c.chave == "backup")
    assert cartao.estado == "nenhum backup baixado"


def test_exportar_devolve_um_zip(logado, db):
    r = logado.post("/gestao/configuracao/backup/exportar")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"
    assert r.headers["content-disposition"].endswith('.zip"')
    assert db.scalars(select(Auditoria).where(
        Auditoria.entidade == "exportacao")).all() != []


def test_exportar_sem_marcar_a_caixa_nao_leva_cpf(logado):
    import io
    import zipfile

    r = logado.post("/gestao/configuracao/backup/exportar")
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert b"11122233344" not in zf.read("militares.csv")

    r = logado.post("/gestao/configuracao/backup/exportar", data={"dados_pessoais": "1"})
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert b"11122233344" in zf.read("militares.csv")


def test_conferir_sem_escolher_arquivo_vira_mensagem(logado):
    """Clicar no botão sem anexar nada é engano comum — não pode ser 422."""
    r = logado.post("/gestao/configuracao/backup/restaurar")
    assert r.status_code == 400
    assert "Escolha o arquivo" in r.text


def test_conferir_arquivo_invalido_explica_o_motivo(logado, tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config.settings, "database_url",
                        f"sqlite+pysqlite:///{(tmp_path / 'e.sqlite3').as_posix()}")
    r = logado.post("/gestao/configuracao/backup/restaurar",
                    files={"arquivo": ("planilha.sqlite3", b"%PDF-1.4", "application/octet-stream")})
    assert r.status_code == 400
    assert "não é um banco SQLite" in r.text


def test_a_conferencia_mostra_o_retrato_e_o_aviso_de_acesso(
        logado, tmp_path, monkeypatch):
    """A tela tem de dizer, antes do clique, que o login some com a restauração."""
    from app import config

    monkeypatch.setattr(config.settings, "database_url",
                        f"sqlite+pysqlite:///{(tmp_path / 'e.sqlite3').as_posix()}")
    r = logado.post(
        "/gestao/configuracao/backup/restaurar",
        files={"arquivo": ("bk.sqlite3", _outro_backup(tmp_path, "9BI", 3),
                           "application/octet-stream")})
    assert r.status_code == 200
    assert "9BI" in r.text
    assert "não existe como" in r.text          # o login logado não está no backup
    assert "Restaurar substitui TUDO" in r.text


# --- 6. cópias automáticas ("preciso do estado de ontem") ---------------------
def test_o_automatico_do_dia_e_gerado_uma_vez_so(instalacao_em_arquivo):
    """Um por dia, não um por boot: `restart: unless-stopped` reinicia o
    container, e cópias do mesmo estado empurrariam os dias anteriores para fora."""
    primeiro = bkp.gerar_automatico()
    assert primeiro is not None and primeiro.exists()
    assert primeiro.name == bkp.nome_automatico(date.today())
    assert bkp.gerar_automatico() is None            # já existe o de hoje
    assert len(bkp.automaticos()) == 1


def test_o_automatico_e_um_banco_com_os_dados(instalacao_em_arquivo):
    arquivo = bkp.gerar_automatico()
    r = bkp.inspecionar(arquivo)
    assert r.om == "1º BI" and r.militares == 2
    assert not list(arquivo.parent.glob("*.parcial"))   # nada de cópia truncada


def test_a_poda_guarda_so_os_ultimos_sete(instalacao_em_arquivo):
    """Sete dias: cobre 'apaguei o mês errado' e a troca de máquina."""
    pasta = bkp.pasta_automaticos(criar=True)     # aqui o TESTE é quem grava
    for dia in range(1, 13):
        (pasta / bkp.nome_automatico(date(2026, 7, dia))).write_bytes(b"x")
    assert bkp.podar_automaticos() == 12 - bkp.MANTER_AUTOMATICOS
    dias = [a.dia.day for a in bkp.automaticos()]
    assert dias == [12, 11, 10, 9, 8, 7, 6]          # do mais recente para trás


def test_gerar_o_de_hoje_derruba_o_mais_antigo(instalacao_em_arquivo):
    pasta = bkp.pasta_automaticos(criar=True)
    velhos = [date(2020, 1, d) for d in range(1, bkp.MANTER_AUTOMATICOS + 1)]
    for dia in velhos:
        (pasta / bkp.nome_automatico(dia)).write_bytes(b"x")
    bkp.gerar_automatico()
    assert len(bkp.automaticos()) == bkp.MANTER_AUTOMATICOS
    assert not (pasta / bkp.nome_automatico(velhos[0])).exists()


def test_listar_nao_cria_pasta_nenhuma(instalacao_em_arquivo):
    """Só quem GRAVA cria diretório.

    A primeira versão criava a pasta ao montar a tela, e bastou renderizar a
    página numa configuração de teste para aparecer um `backups/` vazio na raiz
    do projeto de quem desenvolve.
    """
    pasta = bkp.pasta_automaticos()
    assert pasta is not None and not pasta.exists()
    assert bkp.automaticos() == []
    assert not pasta.exists()
    with pytest.raises(bkp.ErroBackup):
        bkp.arquivo_automatico("escala-2026-07-28.sqlite3")
    assert not pasta.exists()

    bkp.gerar_automatico()
    assert pasta.is_dir()


def test_nome_de_automatico_fora_do_padrao_nao_alcanca_arquivo(instalacao_em_arquivo):
    """A validação é por FORMATO: não existe nome com barra que case."""
    bkp.gerar_automatico()
    for ruim in ("../escala.sqlite3", "escala.sqlite3", "escala-2026-13-99.sqlite3",
                 "", "escala-2026-07-28.sqlite3.bak"):
        with pytest.raises(bkp.ErroBackup, match="não encontrado"):
            bkp.arquivo_automatico(ruim)
    assert bkp.arquivo_automatico(bkp.nome_automatico(date.today())).is_file()


def test_o_automatico_carimba_a_versao_da_aplicacao(instalacao_em_arquivo):
    """Sem o carimbo, decidir 'este arquivo serve?' dependeria de ler um hash."""
    from app import VERSAO

    assert bkp.inspecionar(bkp.gerar_automatico()).versao == VERSAO


def test_backup_antigo_sem_carimbo_nao_quebra_a_conferencia(banco_em_arquivo):
    r = bkp.inspecionar(banco_em_arquivo)
    assert r.versao == "" and r.versao_atual != ""


def test_baixar_carimba_a_versao_dentro_do_arquivo(logado, db, tmp_path):
    """O carimbo tem de estar DENTRO do que se baixa, não só no banco de origem."""
    from app import VERSAO

    conteudo = logado.post("/gestao/configuracao/backup/baixar").content
    arq = tmp_path / "baixado.sqlite3"
    arq.write_bytes(conteudo)
    con = sqlite3.connect(arq)
    assert con.execute("SELECT valor FROM configuracao WHERE chave = "
                       "'versao_aplicacao'").fetchone()[0] == VERSAO
    con.close()


# --- 7. a máquina nova (restaurar antes de existir gestor) --------------------
@pytest.fixture()
def maquina_nova(tmp_path, monkeypatch):
    """Instalação recém-subida: tabelas e referências, NENHUM gestor.

    Devolve `(cliente, caminho_do_banco, senha_de_instalacao)` — a senha é
    exigida pela porta de restauração, que é justamente o que impede qualquer
    um na rede da OM de substituir a instalação (ver
    `test_primeiro_acesso_senha.py`).
    """
    from sqlalchemy.orm import sessionmaker

    from app import config, database
    from app.services.instalacao import senha_primeiro_acesso

    caminho = tmp_path / "nova.sqlite3"
    url = f"sqlite+pysqlite:///{caminho.as_posix()}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed_circulos(s)
        seed_postos_graduacao(s)
        s.commit()
    _marcar_versao(caminho)

    monkeypatch.setattr(config.settings, "database_url", url)
    monkeypatch.setattr(config.settings, "primeiro_acesso_file",
                        str(tmp_path / "primeiro-acesso.txt"))
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal",
                        sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))
    sessao = Session(engine)
    app.dependency_overrides[get_db] = lambda: sessao
    with TestClient(app) as c:
        yield c, caminho, senha_primeiro_acesso()
    app.dependency_overrides.clear()
    sessao.close()
    engine.dispose()


def test_o_primeiro_acesso_oferece_restaurar(maquina_nova):
    """Metade dos primeiros acessos não é instalação nova: é máquina trocada."""
    cliente, _, _ = maquina_nova
    corpo = cliente.get("/gestao/primeiro-acesso").text
    assert "/gestao/restaurar-instalacao" in corpo
    assert cliente.get("/gestao/restaurar-instalacao").status_code == 200


def test_restaurar_na_maquina_nova_traz_o_estado_de_outra(maquina_nova, tmp_path):
    """O cenário inteiro: máquina B sobe vazia e assume o estado da máquina A."""
    cliente, caminho, senha = maquina_nova
    conferencia = cliente.post(
        "/gestao/restaurar-instalacao",
        data={"senha_instalacao": senha},
        files={"arquivo": ("bk.sqlite3", _outro_backup(tmp_path, "9BI", 4),
                           "application/octet-stream")})
    assert conferencia.status_code == 200
    assert "9BI" in conferencia.text
    # No banco vazio não há o que perder — a tela diz isso, e diz como entrar.
    assert "nada a perder" in conferencia.text
    # A senha viaja para a etapa 2: quem a acertou aqui já provou ter o servidor.
    assert f'name="senha_instalacao" value="{senha}"' in conferencia.text

    token = conferencia.text.split('name="token" value="')[1].split('"')[0]
    r = cliente.post("/gestao/restaurar-instalacao/confirmar",
                     data={"token": token, "senha_instalacao": senha},
                     follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/gestao/login?ok=instalacao-restaurada"

    con = sqlite3.connect(caminho)
    assert con.execute("SELECT COUNT(*) FROM militar").fetchone()[0] == 4
    assert con.execute("SELECT login FROM usuario").fetchone()[0] == "outro"
    # Regra 11: um ato que substitui a instalação inteira deixa linha, mesmo sem
    # autor — quem restaurou não tinha login nenhum.
    autor, depois = con.execute(
        "SELECT usuario_id, dados_depois FROM auditoria WHERE entidade = 'backup'"
    ).fetchone()
    assert autor is None and "primeiro acesso" in depois
    con.close()


def test_a_porta_da_maquina_nova_fecha_quando_ha_gestor(instalacao_em_arquivo, client):
    """Aberta enquanto não há gestor; depois, é cadastro aberto — e não pode ser."""
    assert client.get("/gestao/restaurar-instalacao",
                      follow_redirects=False).status_code == 303
    assert client.post("/gestao/restaurar-instalacao",
                       follow_redirects=False).status_code == 303
    assert client.post("/gestao/restaurar-instalacao/confirmar", data={"token": "x"},
                       follow_redirects=False).status_code == 303


def test_o_login_mostra_que_a_senha_e_a_antiga(client):
    """Quem restaurou não tem sessão: é no login que precisa ler isso."""
    corpo = client.get("/gestao/login?ok=instalacao-restaurada").text
    assert "senha que já usavam" in corpo


# --- 8. a tela lista e gera as cópias automáticas ----------------------------
@pytest.fixture()
def logado_em_arquivo(instalacao_em_arquivo, logado):
    """Gestor logado numa instalação com banco em disco (os automáticos existem)."""
    return logado


def test_a_tela_lista_as_copias_automaticas(logado_em_arquivo):
    bkp.gerar_automatico()
    corpo = logado_em_arquivo.get("/gestao/configuracao/backup").text
    assert "Cópias automáticas do dia" in corpo
    assert bkp.nome_automatico(date.today()) in corpo


def test_baixar_uma_copia_automatica(logado_em_arquivo):
    """É como o estado de ontem sai da máquina que está com problema."""
    bkp.gerar_automatico()
    r = logado_em_arquivo.get(
        f"/gestao/configuracao/backup/automatico/{bkp.nome_automatico(date.today())}")
    assert r.status_code == 200 and r.content[:16] == bkp.MAGIC


def test_baixar_automatico_inexistente_nao_derruba_a_tela(logado_em_arquivo):
    r = logado_em_arquivo.get("/gestao/configuracao/backup/automatico/qualquer.sqlite3")
    assert r.status_code == 404
    assert "não encontrado" in r.text


def test_gerar_a_copia_de_hoje_pelo_botao(logado_em_arquivo):
    """Quem vai desligar a máquina agora não espera a próxima passagem do laço."""
    r = logado_em_arquivo.post("/gestao/configuracao/backup/automatico",
                               follow_redirects=False)
    assert r.status_code == 303
    assert "backup-automatico-gerado" in r.headers["location"]
    assert len(bkp.automaticos()) == 1
