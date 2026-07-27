"""Configurações da instalação (regra 13.2 — uma OM por instalação).

O sistema deixou de ser "o QG do CMS": qualquer OM instala a sua, inclusive
batalhão. O que era fixo no código ou no `.env` virou dado editável, e é aqui
que se prova que as recusas certas acontecem — apagar uma graduação em uso
levaria junto o cadastro e o histórico de quem a tem.
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.gestao import Usuario
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import (
    Configuracao, OrganizacaoMilitar, PostoGraduacao, TipoImpedimento,
)
from app.seeds import seed_circulos, seed_postos_graduacao, seed_tipos_impedimento
from app.seeds.usuario import criar_ou_atualizar_gestor
from app.services import configuracao as cfg
from app.web import AVISOS


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


def _sigla(db, nome):
    return db.scalar(select(PostoGraduacao).where(PostoGraduacao.sigla == nome))


# --- 0. o hub de cartões ------------------------------------------------------
def test_o_hub_lista_um_cartao_por_assunto(client):
    texto = client.get("/gestao/configuracao").text
    for caminho in ("/gestao/configuracao/instalacao", "/gestao/configuracao/oms",
                    "/gestao/configuracao/graduacoes", "/gestao/configuracao/tipos",
                    "/gestao/configuracao/gestores", "/gestao/importar"):
        assert f'href="{caminho}"' in texto


def test_cada_cartao_leva_a_uma_pagina_que_existe(client, db):
    """Cartão que aponta para 404 é pior que não ter cartão."""
    for c in cfg.panorama(db):
        assert client.get(c.caminho).status_code == 200, c.caminho


def test_o_cartao_avisa_que_a_om_nao_foi_escolhida(client, db):
    """O hub tem de valer a visita: contagem E pendência, não só o título."""
    cartao = next(c for c in cfg.panorama(db) if c.chave == "instalacao")
    assert cartao.pendencia and "não definida" in cartao.pendencia
    assert "Atenção" in client.get("/gestao/configuracao").text


def test_a_pendencia_some_quando_resolvida(client, db):
    cfg.definir_om_propria(db, 1)
    db.commit()
    cartao = next(c for c in cfg.panorama(db) if c.chave == "instalacao")
    assert not cartao.pendencia and cartao.estado.startswith("QG")


def test_um_gestor_so_e_apontado_como_ponto_unico_de_falha(client, db):
    """Perdida a senha do único gestor, só a TI recria — pelo terminal."""
    cartao = next(c for c in cfg.panorama(db) if c.chave == "gestores")
    assert "só um gestor" in cartao.pendencia
    cfg.criar_gestor(db, "outro", "Outro", "senha-boa-1", "senha-boa-1")
    db.commit()
    assert not next(c for c in cfg.panorama(db) if c.chave == "gestores").pendencia


def test_sem_servico_gravado_o_hub_sugere_importar(client, db):
    cartao = next(c for c in cfg.panorama(db) if c.chave == "importar")
    assert "nunca serviu" in cartao.pendencia


def test_a_pagina_da_secao_repete_o_texto_do_cartao(client, db):
    """O gestor reencontra no cabeçalho a frase em que clicou — fonte única."""
    cartao = next(c for c in cfg.panorama(db) if c.chave == "graduacoes")
    texto = client.get("/gestao/configuracao/graduacoes").text
    assert cartao.titulo in texto and cartao.descricao[:40] in texto


def test_a_secao_tem_volta_para_o_hub(client):
    assert 'href="/gestao/configuracao"' in client.get(
        "/gestao/configuracao/oms").text


def test_gravar_volta_para_a_propria_secao_e_nao_para_o_hub(client):
    """Quem acabou de cadastrar uma OM normalmente vai cadastrar a próxima."""
    r = client.post("/gestao/configuracao/oms", follow_redirects=False,
                             data={"nome": "Nova OM", "sigla": "N OM"})
    assert r.headers["location"] == "/gestao/configuracao/oms?ok=om-criada"


def test_os_icones_nao_dependem_de_nada_externo(client):
    """A rede da OM pode não ter internet: sem CDN, sem fonte de ícones."""
    texto = client.get("/gestao/configuracao").text
    assert "<symbol id=\"i-predio\"" in texto
    assert "http://" not in texto.replace("http://www.w3.org", "")
    assert "https://" not in texto


# --- 1. OM da instalação ------------------------------------------------------
def test_sem_om_marcada_o_cabecalho_cai_no_env(db):
    ident = cfg.identificacao(db)
    assert ident.configurada is False and ident.sigla   # veio do .env, não vazio


def test_marcar_a_om_da_casa(client, db):
    client.post("/gestao/configuracao/instalacao", data={"om_id": 1, "suporte_contato": ""},
                follow_redirects=False)
    db.expire_all()
    assert cfg.identificacao(db).sigla == "QG"
    assert cfg.identificacao(db).configurada is True


def test_so_uma_om_e_a_da_casa(client, db):
    """Uma instalação por OM: marcar a segunda desmarca a primeira."""
    client.post("/gestao/configuracao/instalacao", data={"om_id": 1}, follow_redirects=False)
    client.post("/gestao/configuracao/instalacao", data={"om_id": 2}, follow_redirects=False)
    db.expire_all()
    proprias = db.scalars(select(OrganizacaoMilitar)
                          .where(OrganizacaoMilitar.propria.is_(True))).all()
    assert [o.sigla for o in proprias] == ["B Ap"]


def test_cabecalho_mostra_a_om_configurada(client, db):
    cfg.definir_om_propria(db, 2)
    db.commit()
    texto = client.get("/").text
    assert "Batalhão de Apoio" in texto and "B Ap" in texto


def test_contato_do_suporte_aparece_no_rodape(client, db):
    client.post("/gestao/configuracao/instalacao",
                data={"om_id": 1, "suporte_contato": "Seção de TI — ramal 1234"},
                follow_redirects=False)
    assert "ramal 1234" in client.get("/").text


def test_chave_desconhecida_nao_entra_no_banco(db):
    with pytest.raises(cfg.ErroConfiguracao):
        cfg.definir(db, "apagar_tudo", "sim")
    assert db.get(Configuracao, "apagar_tudo") is None


# --- 2. OMs -------------------------------------------------------------------
def test_cadastrar_om(client, db):
    client.post("/gestao/configuracao/oms",
                data={"nome": "1º Centro de Geoinformação", "sigla": "1º CGeo"},
                follow_redirects=False)
    assert db.scalar(select(OrganizacaoMilitar)
                     .where(OrganizacaoMilitar.sigla == "1º CGeo")) is not None


def test_sigla_repetida_e_recusada(client, db):
    r = client.post("/gestao/configuracao/oms", data={"nome": "Outra", "sigla": "qg"})
    assert r.status_code == 400 and "sigla" in r.text.lower()


def test_om_com_militar_nao_pode_ser_excluida(client, db):
    r = client.post("/gestao/configuracao/oms/1/excluir")
    assert r.status_code == 400 and "militar" in r.text.lower()
    assert db.get(OrganizacaoMilitar, 1) is not None


def test_om_da_casa_nao_pode_ser_excluida(client, db):
    cfg.definir_om_propria(db, 2)
    db.commit()
    r = client.post("/gestao/configuracao/oms/2/excluir")
    assert r.status_code == 400 and db.get(OrganizacaoMilitar, 2) is not None


def test_om_vazia_pode_ser_excluida(client, db):
    client.post("/gestao/configuracao/oms/2/excluir", follow_redirects=False)
    assert db.get(OrganizacaoMilitar, 2) is None


# --- 3. postos e graduações ---------------------------------------------------
def test_acrescentar_graduacao_entra_na_posicao_pedida(client, db):
    """'Abaixo de Cb' = mais moderna que o Cabo e mais antiga que o Soldado."""
    cb, sd = _sigla(db, "Cb"), _sigla(db, "Sd")
    client.post("/gestao/configuracao/graduacoes", follow_redirects=False, data={
        "sigla": "Cb Mor", "nome": "Cabo-Mor", "circulo_id": cb.circulo_id,
        "abaixo_de_id": cb.id,
    })
    db.expire_all()
    nova = _sigla(db, "Cb Mor")
    assert _sigla(db, "Cb").ordem_hierarquica > nova.ordem_hierarquica > _sigla(db, "Sd").ordem_hierarquica


def test_mover_troca_com_a_vizinha(client, db):
    """Subir = ficar mais antigo. A ordem é o critério 9.1 do desempate."""
    antes_cap = _sigla(db, "Cap").ordem_hierarquica
    antes_maj = _sigla(db, "Maj").ordem_hierarquica
    assert antes_maj > antes_cap
    client.post(f"/gestao/configuracao/graduacoes/{_sigla(db, 'Cap').id}/mover",
                data={"direcao": "subir"}, follow_redirects=False)
    db.expire_all()
    assert _sigla(db, "Cap").ordem_hierarquica > _sigla(db, "Maj").ordem_hierarquica


def test_a_ordem_nunca_empata(client, db):
    """Empate tornaria o desempate 9.1 arbitrário — a renumeração impede."""
    client.post(f"/gestao/configuracao/graduacoes/{_sigla(db, 'Cap').id}/mover",
                data={"direcao": "subir"}, follow_redirects=False)
    db.expire_all()
    ordens = [g.ordem_hierarquica for g in cfg.graduacoes(db)]
    assert len(ordens) == len(set(ordens))


def test_no_topo_nao_sobe_mais(client, db):
    topo = cfg.graduacoes(db)[0]
    r = client.post(f"/gestao/configuracao/graduacoes/{topo.id}/mover",
                    data={"direcao": "subir"})
    assert r.status_code == 400 and "limite" in r.text.lower()


def test_graduacao_em_uso_nao_pode_ser_excluida(client, db):
    """Apagar levaria junto o militar (FK) e o histórico dele."""
    cap = _sigla(db, "Cap")
    r = client.post(f"/gestao/configuracao/graduacoes/{cap.id}/excluir")
    assert r.status_code == 400 and "desative" in r.text.lower()
    assert db.get(PostoGraduacao, cap.id) is not None


def test_desativar_nao_mexe_em_quem_ja_tem(client, db):
    cap = _sigla(db, "Cap")
    client.post(f"/gestao/configuracao/graduacoes/{cap.id}/situacao",
                data={"ativo": "0"}, follow_redirects=False)
    db.expire_all()
    assert _sigla(db, "Cap").ativo is False
    assert db.get(Militar, 1).posto_graduacao_id == cap.id


def test_graduacao_desativada_some_do_formulario(client, db):
    ten = _sigla(db, "1º Ten")
    client.post(f"/gestao/configuracao/graduacoes/{ten.id}/situacao",
                data={"ativo": "0"}, follow_redirects=False)
    texto = client.get("/gestao/militares/novo").text
    assert f'value="{ten.id}"' not in texto


def test_edicao_mantem_a_graduacao_desativada_do_proprio_militar(client, db):
    """Sem isto, salvar a edição trocaria a patente dele em silêncio."""
    cap = _sigla(db, "Cap")
    client.post(f"/gestao/configuracao/graduacoes/{cap.id}/situacao",
                data={"ativo": "0"}, follow_redirects=False)
    texto = client.get("/gestao/militares/1").text
    assert f'value="{cap.id}"' in texto


def test_a_ordem_editada_e_a_que_o_desempate_usa(client, db):
    """O ponto de todo o refactor: a fila passou a ler a ordem do BANCO.

    Antes, `POSTO_ORDEM` fixo no domínio mandava — uma OM que reordenasse a
    tabela veria o desempate rodar com a ordem antiga, sem aviso nenhum.
    """
    from app.domain.antiguidade import comparar_antiguidade
    from app.services.mapeamento import militar_para_dominio

    maj = _sigla(db, "Maj")
    outro = Militar(id=2, nome_guerra="LIMA", nome_completo="Ana Lima",
                    posto_graduacao_id=maj.id, om_id=1)
    db.add(outro)
    db.commit()

    def carregar(mid):
        m = db.get(Militar, mid)
        return militar_para_dominio(m)

    # Cap é mais moderno que Maj -> vem antes na fila (negativo)
    assert comparar_antiguidade(carregar(1), carregar(2)) < 0

    # o gestor sobe o Cap acima do Maj: a fila tem de inverter
    client.post(f"/gestao/configuracao/graduacoes/{_sigla(db, 'Cap').id}/mover",
                data={"direcao": "subir"}, follow_redirects=False)
    db.expire_all()
    assert comparar_antiguidade(carregar(1), carregar(2)) > 0


# --- 4. tipos de impedimento --------------------------------------------------
def test_criar_tipo_de_impedimento(client, db):
    client.post("/gestao/configuracao/tipos", data={"nome": "Júri"}, follow_redirects=False)
    assert db.scalar(select(TipoImpedimento).where(TipoImpedimento.nome == "Júri")) is not None


def test_tipo_em_uso_nao_pode_ser_excluido(client, db):
    tipo = db.scalar(select(TipoImpedimento))
    db.add(Impedimento(militar_id=1, tipo_impedimento_id=tipo.id,
                       inicio=date(2026, 7, 1), fim=date(2026, 7, 5)))
    db.commit()
    r = client.post(f"/gestao/configuracao/tipos/{tipo.id}/excluir")
    assert r.status_code == 400 and db.get(TipoImpedimento, tipo.id) is not None


def test_tipo_desativado_some_do_formulario_de_impedimento(client, db):
    tipo = db.scalar(select(TipoImpedimento))
    client.post(f"/gestao/configuracao/tipos/{tipo.id}/situacao", data={"ativo": "0"},
                follow_redirects=False)
    texto = client.get("/gestao/impedimentos").text
    assert 'name="tipo_impedimento_id"' in texto     # o campo continua lá
    assert f">{tipo.nome}</option>" not in texto     # mas sem o tipo desativado


# --- 5. gestores (regra 11) ---------------------------------------------------
def test_cadastrar_gestor_pela_interface(client, db):
    """Era a última coisa que só existia no CLI."""
    client.post("/gestao/configuracao/gestores", follow_redirects=False, data={
        "login": "sgt.silva", "nome": "Sgt Silva",
        "senha": "outra-senha-1", "senha2": "outra-senha-1"})
    novo = db.scalar(select(Usuario).where(Usuario.login == "sgt.silva"))
    assert novo is not None and novo.ativo


def test_gestor_novo_consegue_entrar(client, db):
    client.post("/gestao/configuracao/gestores", follow_redirects=False, data={
        "login": "sgt.silva", "nome": "Sgt Silva",
        "senha": "outra-senha-1", "senha2": "outra-senha-1"})
    client.get("/gestao/logout", follow_redirects=False)
    r = client.post("/gestao/login", follow_redirects=False,
                    data={"username": "sgt.silva", "password": "outra-senha-1"})
    assert r.status_code == 303 and "login" not in r.headers["location"]


def test_senhas_diferentes_sao_recusadas(client, db):
    r = client.post("/gestao/configuracao/gestores", data={
        "login": "x", "nome": "X", "senha": "senha-boa-1", "senha2": "senha-boa-2"})
    assert r.status_code == 400 and "conferem" in r.text.lower()


def test_senha_curta_e_recusada(client, db):
    r = client.post("/gestao/configuracao/gestores", data={
        "login": "x", "nome": "X", "senha": "curta", "senha2": "curta"})
    assert r.status_code == 400 and db.scalar(
        select(Usuario).where(Usuario.login == "x")) is None


def test_login_repetido_e_recusado(client, db):
    r = client.post("/gestao/configuracao/gestores", data={
        "login": "BRIGADA", "nome": "Outro", "senha": "senha-boa-1", "senha2": "senha-boa-1"})
    assert r.status_code == 400 and "login" in r.text.lower()


def test_nao_se_pode_desativar_o_proprio_acesso(client, db):
    eu = db.scalar(select(Usuario).where(Usuario.login == "brigada"))
    r = client.post(f"/gestao/configuracao/gestores/{eu.id}/situacao", data={"ativo": "0"})
    assert r.status_code == 400 and "próprio" in r.text.lower()


def test_nao_se_pode_desativar_o_ultimo_gestor(client, db):
    """A instalação ficaria sem quem a administra — e sem como voltar pela tela."""
    outro = cfg.criar_gestor(db, "outro", "Outro", "senha-boa-1", "senha-boa-1")
    db.commit()
    r = client.post(f"/gestao/configuracao/gestores/{outro.id}/situacao",
                    data={"ativo": "0"}, follow_redirects=False)
    assert r.status_code == 303          # esse pode: sobra o brigada
    eu = db.scalar(select(Usuario).where(Usuario.login == "brigada"))
    eu.ativo = False                     # simula o brigada já desativado
    db.commit()
    with pytest.raises(cfg.ErroConfiguracao):
        cfg.definir_gestor_ativo(db, outro.id, False, quem_pede=999)


def test_trocar_a_senha_de_outro_gestor(client, db):
    outro = cfg.criar_gestor(db, "outro", "Outro", "senha-boa-1", "senha-boa-1")
    db.commit()
    client.post(f"/gestao/configuracao/gestores/{outro.id}/senha", follow_redirects=False,
                data={"senha": "senha-nova-9", "senha2": "senha-nova-9"})
    client.get("/gestao/logout", follow_redirects=False)
    r = client.post("/gestao/login", follow_redirects=False,
                    data={"username": "outro", "password": "senha-nova-9"})
    assert r.status_code == 303 and "login" not in r.headers["location"]


def test_senha_nunca_entra_na_auditoria(client, db):
    from app.models.gestao import Auditoria
    client.post("/gestao/configuracao/gestores", follow_redirects=False, data={
        "login": "sgt.silva", "nome": "Sgt Silva",
        "senha": "segredo-do-sgt", "senha2": "segredo-do-sgt"})
    registros = db.scalars(select(Auditoria).where(Auditoria.entidade == "usuario")).all()
    assert registros
    for r in registros:
        assert "segredo-do-sgt" not in str(r.dados_depois)
        assert "senha_hash" not in str(r.dados_depois)


# --- confirmação --------------------------------------------------------------
def test_toda_confirmacao_da_tela_tem_traducao():
    """Chave sem tradução vira silêncio: a ação acontece e a tela não diz nada."""
    import re
    from pathlib import Path
    fonte = (Path(__file__).resolve().parents[1] / "app" / "web"
             / "gestao_config.py").read_text(encoding="utf-8")
    chaves = re.findall(r'_ok\(\s*"[a-z]+",\s*\n?\s*"([a-z-]+)"', fonte)
    assert chaves, "nenhuma chave encontrada — o teste perdeu o alvo"
    for chave in chaves:
        assert chave in AVISOS, f"'{chave}' não está em AVISOS"
