"""Assistente de primeira execução: o que falta para a OM começar a usar.

O sistema já tinha todas as telas para se instalar sozinho (Configurações,
cadastro do efetivo, escalas, importação do histórico), mas **nenhuma delas
diz por onde começar**. Quem recebe a imagem descobria a ordem certa lendo o
manual — ou tentando, e batendo em "escala sem participantes".

A ordem aqui é a MESMA do hub de Configurações e do manual, e não é estética:
cada passo depende do anterior. Não há como marcar a OM da casa antes de
cadastrá-la, nem escalar antes de ter efetivo, nem importar histórico antes de
existir a escala que recebe os serviços.

Como o painel (`painel.py`), este módulo **não reimplementa regra**: lê o que
está gravado e responde "isto já foi feito?".
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.escala import Escala, Participacao, Posto
from app.models.gestao import Usuario
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.models.servico import Servico
from app.services import configuracao as cfg


@dataclass(frozen=True)
class Passo:
    """Um passo da instalação, do jeito que a tela o apresenta.

    `feito` é o fato; `estado` é o que já existe; `porque` é a consequência de
    pular — sem isso o passo opcional parece dispensável, e não é.
    """
    chave: str
    titulo: str
    porque: str
    caminho: str
    rotulo_acao: str
    feito: bool
    estado: str = ""
    opcional: bool = False           # não impede de usar, mas é fortemente sugerido


def passos(db: Session) -> list[Passo]:
    """Os passos da instalação, na ordem em que dependem uns dos outros."""
    ident = cfg.identificacao(db)
    n_oms = db.scalar(select(func.count()).select_from(OrganizacaoMilitar)) or 0
    n_grads = db.scalar(select(func.count()).select_from(PostoGraduacao)
                        .where(PostoGraduacao.ativo.is_(True))) or 0
    n_militares = db.scalar(select(func.count()).select_from(Militar)
                            .where(Militar.ativo.is_(True))) or 0
    n_servicos = db.scalar(select(func.count()).select_from(Servico)) or 0
    n_gestores = db.scalar(select(func.count()).select_from(Usuario)
                           .where(Usuario.ativo.is_(True))) or 0

    # Escala "pronta para rodar" = ativa, com ao menos um posto e um
    # participante ativo. Escala sem posto não escala ninguém, e escala sem
    # participante não tem fila — nos dois casos o mês fecha vazio (regra 7.8).
    prontas = db.scalar(
        select(func.count()).select_from(Escala).where(
            Escala.ativa.is_(True),
            select(func.count()).select_from(Posto)
            .where(Posto.escala_id == Escala.id).scalar_subquery() > 0,
            select(func.count()).select_from(Participacao)
            .where(Participacao.escala_id == Escala.id,
                   Participacao.ativo.is_(True)).scalar_subquery() > 0,
        )
    ) or 0
    n_escalas = db.scalar(select(func.count()).select_from(Escala)
                          .where(Escala.ativa.is_(True))) or 0

    return [
        Passo(
            "instalacao", "Dizer qual é a OM desta casa",
            "É o que aparece no cabeçalho de todas as telas e no documento "
            "impresso. Sem isso o sistema se apresenta com o nome do arquivo "
            "de configuração, que não é o da sua OM.",
            "/gestao/configuracao/instalacao", "Definir a OM",
            feito=ident.configurada,
            estado=f"{ident.sigla} — {ident.nome}" if ident.configurada else "",
        ),
        Passo(
            "graduacoes", "Conferir postos e graduações",
            "A ordem hierárquica desta tabela é o desempate da fila (regra 9.1). "
            "Já vem preenchida pela Lei 6.880/80: só confira, e desative o que "
            "a sua OM não usa.",
            "/gestao/configuracao/graduacoes", "Conferir",
            feito=n_grads > 0,
            estado=f"{n_grads} em uso",
        ),
        Passo(
            "oms", "Cadastrar as OMs de origem",
            "De onde vem quem serve aqui (regra 3.2). Num QG o efetivo vem de "
            "várias OMs; num batalhão, quase sempre de uma só.",
            "/gestao/configuracao/oms", "Cadastrar",
            feito=n_oms > 0,
            estado=f"{n_oms} cadastrada(s)",
        ),
        Passo(
            "efetivo", "Cadastrar o efetivo",
            "Quem pode entrar de serviço. Dá para digitar ou importar a ficha "
            "individual em PDF, que pré-preenche o formulário.",
            "/gestao/militares/novo", "Cadastrar militar",
            feito=n_militares > 0,
            estado=f"{n_militares} militar(es) ativo(s)",
        ),
        Passo(
            "escalas", "Criar as escalas",
            "Cada escala carrega as cores em que roda, os postos por dia, os "
            "participantes e a folga mínima (regra 4.2). Uma escala sem posto "
            "ou sem participante não escala ninguém.",
            "/gestao/escalas/nova", "Criar escala",
            feito=prontas > 0,
            estado=(f"{prontas} pronta(s) para rodar"
                    + (f", {n_escalas - prontas} incompleta(s)" if n_escalas > prontas else "")
                    if n_escalas else ""),
        ),
        Passo(
            "historico", "Carregar o histórico de serviços",
            "Sem ele o motor começa com todo mundo empatado em 'nunca serviu', "
            "e a primeira escalação sai só pela antiguidade — ignorando quem "
            "acabou de deixar o serviço. Só faz sentido se a OM já tem passado "
            "em planilha.",
            "/gestao/importar", "Importar CSV",
            feito=n_servicos > 0,
            estado=f"{n_servicos} serviço(s) registrado(s)",
            opcional=True,
        ),
        Passo(
            "gestores", "Cadastrar um segundo gestor",
            "Com um gestor só, perder a senha significa depender da seção de TI "
            "para recriar o acesso. O segundo custa um minuto (regra 11).",
            "/gestao/configuracao/gestores", "Cadastrar gestor",
            feito=n_gestores > 1,
            estado=f"{n_gestores} gestor(es) ativo(s)",
            opcional=True,
        ),
    ]


def pendentes(lista: list[Passo]) -> list[Passo]:
    return [p for p in lista if not p.feito]


def concluida(lista: list[Passo]) -> bool:
    """Instalação utilizável: os passos obrigatórios estão todos feitos.

    O histórico e o segundo gestor NÃO entram: quem instala numa OM sem passado
    em planilha nunca teria a instalação 'completa', e o aviso viraria ruído
    permanente — que é como um aviso perde o sentido.
    """
    return all(p.feito for p in lista if not p.opcional)


def proximo(lista: list[Passo]) -> Passo | None:
    """O próximo passo a fazer — obrigatório antes de opcional."""
    faltando = pendentes(lista)
    return next((p for p in faltando if not p.opcional), faltando[0] if faltando else None)


def sem_gestor(db: Session) -> bool:
    """Instalação recém-subida: ninguém pode entrar ainda (regra 11).

    É o que autoriza a tela de primeiro acesso a existir — e o que a fecha
    assim que o primeiro gestor é criado.
    """
    return db.scalar(select(func.count()).select_from(Usuario)) == 0
