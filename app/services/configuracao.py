"""Configuração da instalação: OM da casa, tabelas de referência e gestores.

O sistema nasceu para o QG do CMS e passou a servir a QUALQUER OM, inclusive
batalhão (regra 13.2 — uma instalação por OM). O que era fixo no código ou no
`.env` vira dado editável pelo gestor:

  - **qual é a OM da casa** (cabeçalho e rodapé das telas);
  - **postos/graduações**, porque a OM pode não ter uma graduação, acrescentar
    outra ou usar nomenclatura própria;
  - **tipos de impedimento**, que variam muito de OM para OM;
  - **gestores** (regra 11 fala em múltiplos gestores; até aqui só pelo CLI).

Regras que este módulo faz valer, e o porquê de cada uma:

  1. Só UMA OM é `propria`. Marcar uma desmarca as outras na mesma transação.
  2. Nada de referência é APAGADO quando está em uso — desativa. Apagar
     quebraria a FK do militar/impedimento já cadastrado e levaria junto o
     histórico dele.
  3. `ordem_hierarquica` é renumerada a cada mudança (10, 20, 30...). Só a
     ordem RELATIVA importa para o desempate 9.1; a renumeração evita empate e
     deixa espaço entre os números.
  4. Gestor não se apaga (a auditoria referencia `usuario_id`) e não se pode
     desativar o último ativo — a instalação ficaria sem quem administra.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.gestao import Usuario
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import (
    CirculoHierarquico, Configuracao, OrganizacaoMilitar, PostoGraduacao, TipoImpedimento,
)
from app.security import hash_senha

# Chaves aceitas em `configuracao`, com o valor padrão. Chave fora desta lista
# é ignorada: a tela não deve poder gravar qualquer coisa no banco.
CHAVES: dict[str, str] = {
    # Aparece no rodapé de todas as telas. É a informação que falta quando algo
    # dá errado numa OM e ninguém sabe a quem recorrer.
    "suporte_contato": "",
}

PASSO_ORDEM = 10          # espaçamento da renumeração hierárquica
SENHA_MINIMA = 8          # mesmo piso do CLI (app/seeds/usuario.py)


class ErroConfiguracao(Exception):
    """Recusa com motivo legível para o gestor (não é erro de sistema)."""


# --- panorama (os cartões da página de Configurações) -------------------------
@dataclass(frozen=True)
class Cartao:
    """Um assunto da configuração, do jeito que o hub o apresenta.

    `estado` é a contagem; `pendencia` é o que falta fazer. Um hub que só
    repete títulos custa um clique e não devolve nada — o que faz a página
    valer a visita é dizer, de relance, onde ainda há trabalho.
    """
    chave: str
    titulo: str
    descricao: str
    caminho: str
    icone: str                      # nome do <symbol> no sprite do template
    estado: str = ""
    pendencia: str = ""


def panorama(db: Session) -> list[Cartao]:
    """Os cartões, na ORDEM DE INSTALAÇÃO — é a sequência do manual, e cada
    passo depende do anterior (não dá para marcar a OM da casa antes de
    cadastrá-la, nem importar histórico antes de ter escala e efetivo)."""
    from app.models.servico import Servico          # local: evita ciclo de import

    ident = identificacao(db)
    n_oms = db.scalar(select(func.count()).select_from(OrganizacaoMilitar)) or 0
    grads = graduacoes(db)
    ativas = [g for g in grads if g.ativo]
    tipos = tipos_impedimento(db)
    tipos_ativos = [t for t in tipos if t.ativo]
    us = gestores(db)
    us_ativos = [u for u in us if u.ativo]
    n_servicos = db.scalar(select(func.count()).select_from(Servico)) or 0

    return [
        Cartao(
            "instalacao", "Esta instalação",
            "Qual é a OM desta casa e o contato do suporte local. Aparecem no "
            "cabeçalho e no rodapé de todas as telas.",
            "/gestao/configuracao/instalacao", "predio",
            estado=f"{ident.sigla} — {ident.nome}" if ident.configurada else "",
            pendencia="" if ident.configurada else
                      f"OM não definida (usando '{ident.sigla}', do arquivo de configuração)",
        ),
        Cartao(
            "oms", "Organizações Militares",
            "As OMs de origem de quem serve nesta escala. Num QG, o efetivo "
            "vem de várias (regra 3.2).",
            "/gestao/configuracao/oms", "predios",
            estado=f"{n_oms} cadastrada(s)",
        ),
        Cartao(
            "graduacoes", "Postos e graduações",
            "A escala hierárquica da Lei 6.880/80. Mexer na ordem muda o "
            "desempate da fila (regra 9.1).",
            "/gestao/configuracao/graduacoes", "galoes",
            estado=f"{len(ativas)} em uso"
                   + (f", {len(grads) - len(ativas)} desativado(s)"
                      if len(grads) > len(ativas) else ""),
            pendencia="nenhum posto/graduação em uso" if not ativas else "",
        ),
        Cartao(
            "tipos", "Tipos de impedimento",
            "Dispensa, férias, curso, operação... o que tira o militar da "
            "rotação sem tirar a vez dele (regra 7.5).",
            "/gestao/configuracao/tipos", "calendario",
            estado=f"{len(tipos_ativos)} em uso",
            pendencia="nenhum tipo em uso — não há como lançar impedimento"
                      if not tipos_ativos else "",
        ),
        Cartao(
            "gestores", "Gestores",
            "Quem entra na gestão com login e senha (regra 11). A consulta "
            "continua aberta a todos.",
            "/gestao/configuracao/gestores", "chave",
            estado=f"{len(us_ativos)} ativo(s)",
            # Um gestor só é um ponto único de falha: perdida a senha, só a TI
            # recria, pelo terminal — e a tela impede desativar o último.
            pendencia="só um gestor ativo; se ele perder a senha, apenas a TI "
                      "recria pelo terminal" if len(us_ativos) == 1 else "",
        ),
        Cartao(
            "importar", "Importar histórico",
            "Carga dos serviços que já aconteceram, vindos de planilha. "
            "Confere antes de gravar.",
            "/gestao/importar", "seta-caixa",
            estado=f"{n_servicos} serviço(s) registrado(s)",
            pendencia="nenhum serviço registrado — sem histórico o motor começa "
                      "com todos empatados em \"nunca serviu\"" if not n_servicos else "",
        ),
    ]


# --- chave/valor --------------------------------------------------------------
def valor(db: Session, chave: str) -> str:
    if chave not in CHAVES:
        return ""
    linha = db.get(Configuracao, chave)
    return linha.valor if linha is not None else CHAVES[chave]


def definir(db: Session, chave: str, novo: str) -> None:
    if chave not in CHAVES:
        raise ErroConfiguracao(f"Configuração desconhecida: {chave}.")
    linha = db.get(Configuracao, chave)
    if linha is None:
        db.add(Configuracao(chave=chave, valor=novo.strip()))
    else:
        linha.valor = novo.strip()


# --- OM da casa ---------------------------------------------------------------
@dataclass(frozen=True)
class Identificacao:
    """Como a instalação se apresenta no cabeçalho e no rodapé."""
    sigla: str
    nome: str
    configurada: bool     # False = ainda vem do .env, ninguém escolheu


def identificacao(db: Session) -> Identificacao:
    """OM da casa, com o `.env` de reserva.

    A reserva existe para o PRIMEIRO boot: antes de alguém entrar em
    Configurações não há OM marcada, e o cabeçalho não pode ficar vazio.
    """
    om = db.scalar(select(OrganizacaoMilitar).where(OrganizacaoMilitar.propria.is_(True)))
    if om is None:
        return Identificacao(settings.om_sigla, settings.om_nome, configurada=False)
    return Identificacao(om.sigla, om.nome, configurada=True)


def definir_om_propria(db: Session, om_id: int) -> OrganizacaoMilitar:
    """Marca a OM da casa e desmarca as demais (uma instalação por OM)."""
    om = db.get(OrganizacaoMilitar, om_id)
    if om is None:
        raise ErroConfiguracao("OM não encontrada.")
    for outra in db.scalars(select(OrganizacaoMilitar).where(
            OrganizacaoMilitar.propria.is_(True), OrganizacaoMilitar.id != om_id)):
        outra.propria = False
    om.propria = True
    return om


def criar_om(db: Session, nome: str, sigla: str) -> OrganizacaoMilitar:
    nome, sigla = nome.strip(), sigla.strip()
    if not nome or not sigla:
        raise ErroConfiguracao("Nome e sigla são obrigatórios.")
    if db.scalar(select(OrganizacaoMilitar).where(
            func.lower(OrganizacaoMilitar.sigla) == sigla.lower())):
        raise ErroConfiguracao(f"Já existe uma OM com a sigla '{sigla}'.")
    om = OrganizacaoMilitar(nome=nome, sigla=sigla)
    db.add(om)
    db.flush()
    return om


def alterar_om(db: Session, om_id: int, nome: str, sigla: str) -> OrganizacaoMilitar:
    om = db.get(OrganizacaoMilitar, om_id)
    if om is None:
        raise ErroConfiguracao("OM não encontrada.")
    nome, sigla = nome.strip(), sigla.strip()
    if not nome or not sigla:
        raise ErroConfiguracao("Nome e sigla são obrigatórios.")
    conflito = db.scalar(select(OrganizacaoMilitar).where(
        func.lower(OrganizacaoMilitar.sigla) == sigla.lower(),
        OrganizacaoMilitar.id != om_id))
    if conflito is not None:
        raise ErroConfiguracao(f"Já existe uma OM com a sigla '{sigla}'.")
    om.nome, om.sigla = nome, sigla
    return om


def militares_por_om(db: Session) -> dict[int, int]:
    """Quantos militares cada OM tem — a tela precisa disso para dizer por que
    não dá para excluir."""
    return {om_id: n for om_id, n in db.execute(
        select(Militar.om_id, func.count()).group_by(Militar.om_id)).all()}


def excluir_om(db: Session, om_id: int) -> None:
    """Só some OM SEM militar e que não seja a da casa."""
    om = db.get(OrganizacaoMilitar, om_id)
    if om is None:
        raise ErroConfiguracao("OM não encontrada.")
    if om.propria:
        raise ErroConfiguracao(
            "Esta é a OM da instalação. Marque outra como a OM da casa antes de excluí-la.")
    usados = db.scalar(select(func.count()).select_from(Militar)
                       .where(Militar.om_id == om_id)) or 0
    if usados:
        raise ErroConfiguracao(
            f"{usados} militar(es) pertencem a esta OM. Mova-os antes de excluí-la.")
    db.delete(om)


# --- postos e graduações ------------------------------------------------------
def graduacoes(db: Session, *, so_ativas: bool = False) -> list[PostoGraduacao]:
    """Da mais antiga para a mais moderna — a ordem em que a hierarquia se lê."""
    stmt = select(PostoGraduacao).order_by(PostoGraduacao.ordem_hierarquica.desc())
    if so_ativas:
        stmt = stmt.where(PostoGraduacao.ativo.is_(True))
    return list(db.scalars(stmt))


def militares_por_graduacao(db: Session) -> dict[int, int]:
    return {pg_id: n for pg_id, n in db.execute(
        select(Militar.posto_graduacao_id, func.count())
        .group_by(Militar.posto_graduacao_id)).all()}


def renumerar_graduacoes(db: Session) -> None:
    """Redistribui `ordem_hierarquica` em 10, 20, 30... mantendo a ordem atual.

    Chamada depois de toda mudança. Impede empate (que tornaria o desempate 9.1
    arbitrário) sem precisar de UNIQUE no banco — a checagem imediata do SQLite
    impediria trocar duas linhas de lugar numa transação só.
    """
    linhas = list(db.scalars(
        select(PostoGraduacao).order_by(PostoGraduacao.ordem_hierarquica.asc(),
                                        PostoGraduacao.id.asc())))
    for i, pg in enumerate(linhas, start=1):
        pg.ordem_hierarquica = i * PASSO_ORDEM
    db.flush()


def criar_graduacao(db: Session, sigla: str, nome: str, circulo_id: int,
                    abaixo_de_id: int | None) -> PostoGraduacao:
    """Cria a graduação logo ABAIXO de outra (mais moderna que ela).

    A posição entra como "abaixo de quem", não como número: o gestor pensa em
    "Cabo-Mor vem abaixo do Cabo", não em "ordem 85".
    """
    sigla, nome = sigla.strip(), nome.strip()
    if not sigla or not nome:
        raise ErroConfiguracao("Sigla e nome são obrigatórios.")
    if db.scalar(select(PostoGraduacao).where(
            func.lower(PostoGraduacao.sigla) == sigla.lower())):
        raise ErroConfiguracao(f"Já existe um posto/graduação '{sigla}'.")
    if db.get(CirculoHierarquico, circulo_id) is None:
        raise ErroConfiguracao("Círculo hierárquico inválido.")

    if abaixo_de_id is None:                 # no topo: mais antigo de todos
        maior = db.scalar(select(func.max(PostoGraduacao.ordem_hierarquica))) or 0
        ordem = maior + PASSO_ORDEM
    else:
        acima = db.get(PostoGraduacao, abaixo_de_id)
        if acima is None:
            raise ErroConfiguracao("Posição de referência inválida.")
        ordem = acima.ordem_hierarquica - 1   # a renumeração acerta o espaçamento

    pg = PostoGraduacao(sigla=sigla, nome=nome, circulo_id=circulo_id,
                        ordem_hierarquica=ordem, ativo=True)
    db.add(pg)
    db.flush()
    renumerar_graduacoes(db)
    return pg


def alterar_graduacao(db: Session, pg_id: int, sigla: str, nome: str,
                      circulo_id: int) -> PostoGraduacao:
    pg = db.get(PostoGraduacao, pg_id)
    if pg is None:
        raise ErroConfiguracao("Posto/graduação não encontrado.")
    sigla, nome = sigla.strip(), nome.strip()
    if not sigla or not nome:
        raise ErroConfiguracao("Sigla e nome são obrigatórios.")
    conflito = db.scalar(select(PostoGraduacao).where(
        func.lower(PostoGraduacao.sigla) == sigla.lower(), PostoGraduacao.id != pg_id))
    if conflito is not None:
        raise ErroConfiguracao(f"Já existe um posto/graduação '{sigla}'.")
    if db.get(CirculoHierarquico, circulo_id) is None:
        raise ErroConfiguracao("Círculo hierárquico inválido.")
    pg.sigla, pg.nome, pg.circulo_id = sigla, nome, circulo_id
    return pg


def mover_graduacao(db: Session, pg_id: int, direcao: str) -> PostoGraduacao:
    """Troca de lugar com a vizinha ('subir' = ficar mais antiga).

    Mover, e não digitar o número, porque o gestor raciocina em hierarquia. A
    troca vale para a fila daqui em diante: os serviços já gravados não mudam.
    """
    pg = db.get(PostoGraduacao, pg_id)
    if pg is None:
        raise ErroConfiguracao("Posto/graduação não encontrado.")
    if direcao == "subir":
        vizinha = db.scalar(
            select(PostoGraduacao)
            .where(PostoGraduacao.ordem_hierarquica > pg.ordem_hierarquica)
            .order_by(PostoGraduacao.ordem_hierarquica.asc()).limit(1))
    elif direcao == "descer":
        vizinha = db.scalar(
            select(PostoGraduacao)
            .where(PostoGraduacao.ordem_hierarquica < pg.ordem_hierarquica)
            .order_by(PostoGraduacao.ordem_hierarquica.desc()).limit(1))
    else:
        raise ErroConfiguracao("Direção inválida.")
    if vizinha is None:
        raise ErroConfiguracao("Já está no limite da hierarquia.")
    pg.ordem_hierarquica, vizinha.ordem_hierarquica = (
        vizinha.ordem_hierarquica, pg.ordem_hierarquica)
    db.flush()
    renumerar_graduacoes(db)
    return pg


def definir_graduacao_ativa(db: Session, pg_id: int, ativo: bool) -> PostoGraduacao:
    """Desativar esconde dos formulários; NÃO mexe em quem já a tem."""
    pg = db.get(PostoGraduacao, pg_id)
    if pg is None:
        raise ErroConfiguracao("Posto/graduação não encontrado.")
    pg.ativo = ativo
    return pg


def excluir_graduacao(db: Session, pg_id: int) -> None:
    """Só some graduação que NINGUÉM tem. Com militar, o caminho é desativar."""
    pg = db.get(PostoGraduacao, pg_id)
    if pg is None:
        raise ErroConfiguracao("Posto/graduação não encontrado.")
    usados = db.scalar(select(func.count()).select_from(Militar)
                       .where(Militar.posto_graduacao_id == pg_id)) or 0
    if usados:
        raise ErroConfiguracao(
            f"{usados} militar(es) têm este posto/graduação. Desative-o em vez de excluir "
            "— excluir levaria junto o cadastro e o histórico deles.")
    db.delete(pg)
    db.flush()
    renumerar_graduacoes(db)


# --- tipos de impedimento -----------------------------------------------------
def tipos_impedimento(db: Session, *, so_ativos: bool = False) -> list[TipoImpedimento]:
    stmt = select(TipoImpedimento).order_by(TipoImpedimento.nome)
    if so_ativos:
        stmt = stmt.where(TipoImpedimento.ativo.is_(True))
    return list(db.scalars(stmt))


def impedimentos_por_tipo(db: Session) -> dict[int, int]:
    return {tipo_id: n for tipo_id, n in db.execute(
        select(Impedimento.tipo_impedimento_id, func.count())
        .group_by(Impedimento.tipo_impedimento_id)).all()}


def criar_tipo_impedimento(db: Session, nome: str) -> TipoImpedimento:
    nome = nome.strip()
    if not nome:
        raise ErroConfiguracao("O nome é obrigatório.")
    if db.scalar(select(TipoImpedimento).where(
            func.lower(TipoImpedimento.nome) == nome.lower())):
        raise ErroConfiguracao(f"Já existe o tipo '{nome}'.")
    tipo = TipoImpedimento(nome=nome)
    db.add(tipo)
    db.flush()
    return tipo


def definir_tipo_ativo(db: Session, tipo_id: int, ativo: bool) -> TipoImpedimento:
    tipo = db.get(TipoImpedimento, tipo_id)
    if tipo is None:
        raise ErroConfiguracao("Tipo não encontrado.")
    tipo.ativo = ativo
    return tipo


def excluir_tipo_impedimento(db: Session, tipo_id: int) -> None:
    tipo = db.get(TipoImpedimento, tipo_id)
    if tipo is None:
        raise ErroConfiguracao("Tipo não encontrado.")
    usados = db.scalar(select(func.count()).select_from(Impedimento)
                       .where(Impedimento.tipo_impedimento_id == tipo_id)) or 0
    if usados:
        raise ErroConfiguracao(
            f"{usados} impedimento(s) usam este tipo. Desative-o em vez de excluir.")
    db.delete(tipo)


# --- gestores (regra 11) ------------------------------------------------------
def gestores(db: Session) -> list[Usuario]:
    return list(db.scalars(select(Usuario).order_by(Usuario.ativo.desc(), Usuario.nome)))


def _validar_senha(senha: str, repetida: str) -> None:
    if len(senha) < SENHA_MINIMA:
        raise ErroConfiguracao(f"A senha precisa de ao menos {SENHA_MINIMA} caracteres.")
    if senha != repetida:
        raise ErroConfiguracao("As duas senhas não conferem.")


def criar_gestor(db: Session, login: str, nome: str, senha: str, repetida: str) -> Usuario:
    login, nome = login.strip().lower(), nome.strip()
    if not login or not nome:
        raise ErroConfiguracao("Login e nome são obrigatórios.")
    if db.scalar(select(Usuario).where(func.lower(Usuario.login) == login)):
        raise ErroConfiguracao(f"Já existe um gestor com o login '{login}'.")
    _validar_senha(senha, repetida)
    u = Usuario(login=login, nome=nome, senha_hash=hash_senha(senha), ativo=True)
    db.add(u)
    db.flush()
    return u


def trocar_senha(db: Session, usuario_id: int, senha: str, repetida: str) -> Usuario:
    u = db.get(Usuario, usuario_id)
    if u is None:
        raise ErroConfiguracao("Gestor não encontrado.")
    _validar_senha(senha, repetida)
    u.senha_hash = hash_senha(senha)
    return u


def definir_gestor_ativo(db: Session, usuario_id: int, ativo: bool, *,
                         quem_pede: int) -> Usuario:
    """Desativa/reativa um gestor. Nunca apaga: a auditoria aponta para ele.

    Duas recusas, ambas para não perder a chave de casa: ninguém se desativa
    (sairia da própria tela) e o último ativo não pode cair.
    """
    u = db.get(Usuario, usuario_id)
    if u is None:
        raise ErroConfiguracao("Gestor não encontrado.")
    if not ativo:
        if u.id == quem_pede:
            raise ErroConfiguracao("Você não pode desativar o seu próprio acesso.")
        ativos = db.scalar(select(func.count()).select_from(Usuario)
                           .where(Usuario.ativo.is_(True))) or 0
        if ativos <= 1:
            raise ErroConfiguracao(
                "Este é o único gestor ativo — a instalação ficaria sem quem a administra.")
    u.ativo = ativo
    return u
