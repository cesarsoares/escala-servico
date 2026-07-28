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


# --- o manual não pode envelhecer em silêncio --------------------------------
def _abas_da_gestao() -> list[str]:
    """Rótulos das abas, lidos do TEMPLATE — não copiados para cá.

    É o que faz o teste valer: renomear uma aba (já aconteceu duas vezes, por
    falta de espaço na barra) quebra este teste até que o manual acompanhe.
    """
    import re
    html = (RAIZ / "app/web/templates/gestao/base_gestao.html").read_text(encoding="utf-8")
    barra = html.split("</nav>")[0]
    fora = {"Ver consulta", "Sair"}          # ações, não telas documentáveis
    return [r for r in re.findall(r">([A-ZÀ-Ú][^<>{}]*?)</a>", barra) if r.strip() not in fora]


def test_o_manual_cita_todas_as_telas_da_gestao():
    """Tela que existe e o manual não cita é tela que o gestor descobre sozinho."""
    texto = (RAIZ / "docs" / "manual" / "manual.md").read_text(encoding="utf-8").lower()
    faltando = [aba for aba in _abas_da_gestao() if aba.strip().lower() not in texto]
    assert not faltando, f"telas ausentes do manual: {faltando}"


def test_o_manual_nao_manda_o_gestor_a_rota_inexistente():
    """Caminho citado no manual tem de existir de verdade na aplicação."""
    import re
    from app.main import app
    texto = (RAIZ / "docs" / "manual" / "manual.md").read_text(encoding="utf-8")
    rotas = {r.path for r in app.routes if hasattr(r, "path")}
    citados = set(re.findall(r"`(/[a-z0-9/_-]*)`", texto))
    assert citados, "o manual deixou de citar qualquer caminho — teste sem valor"
    assert citados <= rotas, f"caminhos que não existem: {sorted(citados - rotas)}"
