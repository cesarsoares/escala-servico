"""Manual de uso (`/manual`) — aberto e imprimível.

O manual é escrito em `docs/manual/manual.md` e renderizado na tela: uma fonte
só, que se lê no repositório e no navegador. Os testes travam o que quebraria
em silêncio — a rota exigindo login, o arquivo sumindo do pacote, o índice
deixando de sair dos títulos e a folha de impressão sendo trocada.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.web import manual as manual_web

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_o_manual_e_aberto(client):
    """Explica também a consulta, que é aberta (regra 13.1)."""
    r = client.get("/manual", follow_redirects=False)
    assert r.status_code == 200


def test_o_arquivo_fonte_existe_onde_o_codigo_procura():
    """Se o `docs/` sair do pacote, a tela vira 'manual não encontrado'."""
    assert manual_web.ARQUIVO.exists()
    assert manual_web.ARQUIVO == RAIZ / "docs" / "manual" / "manual.md"


def test_o_markdown_vira_html(client):
    texto = client.get("/manual").text
    assert "<h2" in texto and "<table" in texto      # títulos e a tabela da escala
    assert "# Manual de uso" not in texto            # não sobrou markdown cru


def test_o_indice_sai_dos_proprios_titulos(client):
    """Acrescentar uma seção não pode exigir lembrar de atualizar o sumário."""
    texto = client.get("/manual").text
    assert 'class="indice"' in texto
    assert "#1-instalando-numa-om-nova" in texto


def test_usa_a_folha_do_documento_e_nao_a_do_sistema(client):
    """A folha da escala impressa é preparada para monocromática (regra 12)."""
    texto = client.get("/manual").text
    assert "/static/impressao.css" in texto
    assert "/static/style.css" not in texto


def test_o_manual_entra_na_imagem_docker():
    """`docs/` está no .dockerignore (as regras não servem em produção), mas o
    manual É servido pela aplicação — sem reinclusão, `/manual` responde
    'não encontrado' só no container, onde ninguém testa."""
    linhas = [l.strip() for l in
              (RAIZ / ".dockerignore").read_text(encoding="utf-8").splitlines()]
    assert "docs/" not in linhas, "docs/ inteiro barra a reinclusão do manual"
    assert "!docs/manual/" in linhas


def test_o_manual_nao_cita_o_qg_do_cms(client):
    """O sistema roda em qualquer OM: o manual não pode ser da OM que o encomendou."""
    corpo = manual_web.ARQUIVO.read_text(encoding="utf-8")
    assert "QG do CMS" not in corpo


def test_o_cabecalho_do_manual_traz_a_om_da_instalacao(client):
    from app.config import settings
    assert settings.om_nome in client.get("/manual").text


def test_o_rodape_das_telas_leva_ao_manual():
    """O manual só serve se houver como chegar nele de qualquer tela."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.database import Base, get_db

    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        app.dependency_overrides[get_db] = lambda: s
        with TestClient(app) as c:
            assert '<a href="/manual">' in c.get("/").text
        app.dependency_overrides.clear()


def test_arquivo_ausente_nao_derruba_a_pagina(client, monkeypatch):
    monkeypatch.setattr(manual_web, "ARQUIVO", RAIZ / "docs" / "manual" / "nao-existe.md")
    manual_web._cache = None
    r = client.get("/manual")
    assert r.status_code == 200 and "não foi encontrado" in r.text
    manual_web._cache = None


def test_o_texto_editado_vale_sem_reiniciar(tmp_path, client, monkeypatch):
    """Corrigir um texto em produção não pode exigir derrubar o servidor."""
    arquivo = tmp_path / "manual.md"
    arquivo.write_text("# Um\n\nprimeira versão\n", encoding="utf-8")
    monkeypatch.setattr(manual_web, "ARQUIVO", arquivo)
    manual_web._cache = None
    assert "primeira versão" in client.get("/manual").text

    import os
    arquivo.write_text("# Um\n\nsegunda versão\n", encoding="utf-8")
    os.utime(arquivo, (0, 0))            # mtime diferente, garantido
    assert "segunda versão" in client.get("/manual").text
    manual_web._cache = None
