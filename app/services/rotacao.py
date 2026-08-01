"""Serviço de rotação: aplica o motor do domínio sobre os dados do banco.

É a orquestração que faltava entre `app/domain/motor.py` (lógica pura) e o
banco: monta as entradas do domínio (via mapeamento), classifica o dia, chama
`proximos` e — opcionalmente — grava os `servico` resultantes.

Nada de regra nova aqui: a regra mora no domínio. Este módulo só busca dados,
chama o motor e persiste o retrato (regra 6/7).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.calendario import classificar_dia
from app.domain.models import Cor, Militar as MilitarDom
from app.domain.motor import (
    MOTIVO_COR, MOTIVO_FOLGA, MOTIVO_IMPEDIDO, MOTIVO_INATIVO, Preterido, selecionar,
)
from app.models.escala import Escala as EscalaORM, Posto
from app.models.servico import Servico
from app.services import calendario_service, mapeamento


@dataclass
class ResultadoDia:
    """Quem serve numa escala num dia, com o aviso de efetivo insuficiente."""
    escala_id: int
    dia: date
    cor: Cor
    escolhidos: list[MilitarDom]   # na ordem da fila; 1º -> posto de ordem 1
    postos_solicitados: int
    efetivo_insuficiente: bool     # regra 7.8: menos militares do que postos
    # Quantos serviços `gravar_dia` REALMENTE criou (None = ainda não gravado).
    # Difere de len(escolhidos) quando o dia já estava fechado: a gravação é
    # idempotente, então re-escalar sem 'regravar' cria zero.
    servicos_gravados: int | None = None
    # Por que cada participante ficou de fora (regra 7.8): sem isto a tela só
    # sabia dizer que o dia ficou curto, nunca de que adiantava fazer o quê.
    preteridos: list[Preterido] = field(default_factory=list)


# Como cada motivo aparece na tela. Fica aqui, e não no template, porque cada
# frase cita o número da regra — texto que precisa acompanhar a regra quando ela
# muda, e que o Jinja não teria como manter honesto.
# As frases vêm precedidas de um número ("2 ...") e por isso são construções
# invariáveis: "2 impedido no dia" não é português, e concordar em código
# significaria carregar plural de cada uma.
ROTULO_MOTIVO = {
    MOTIVO_IMPEDIDO: "com impedimento no dia — dispensa, férias, curso (regra 7.5)",
    MOTIVO_FOLGA: "sem a folga mínima cumprida (regra 7.4)",
    MOTIVO_COR: "sem concorrer nesta cor (regra 3.3.1)",
    MOTIVO_INATIVO: "com a participação isenta nesta escala (regra 7.6)",
}
# Ordem em que os motivos são listados: primeiro o que muda amanhã (folga),
# depois o que depende de um ato do gestor, por último o que é permanente.
ORDEM_MOTIVO = [MOTIVO_FOLGA, MOTIVO_IMPEDIDO, MOTIVO_COR, MOTIVO_INATIVO]


@dataclass
class FaltaNoDia:
    """Um dia que fechou com menos militares que postos, e por quê (regra 7.8)."""
    dia: date
    cor: Cor
    postos: int
    escalados: int
    por_motivo: list[tuple[str, int]]              # (frase do motivo, quantos)
    liberam_primeiro: list[tuple[MilitarDom, date]]  # quem sai da folga antes

    @property
    def vagas_abertas(self) -> int:
        return self.postos - self.escalados


def explicar_faltas(
    resultados: list[ResultadoDia], limite: int = 20,
) -> tuple[list[FaltaNoDia], int]:
    """Traduz os dias curtos num porquê legível. Devolve (lista, total de dias).

    Conta por motivo em vez de listar nomes: numa escala de 139 participantes,
    um dia curto tem 130 preteridos, e 130 nomes não são informação. O que é
    informação: quantos caem em cada motivo e **quem sai da folga primeiro** —
    a única linha que responde "então o que eu faço?".

    `limite` corta a lista exibida (um ano curto renderia 365 blocos); o total
    volta junto para a tela poder dizer quantos ficaram de fora da lista.
    """
    curtos = [r for r in resultados if r.efetivo_insuficiente]
    saida = []
    for r in curtos[:limite]:
        contagem: dict[str, int] = {}
        for p in r.preteridos:
            contagem[p.motivo] = contagem.get(p.motivo, 0) + 1
        por_motivo = [
            (ROTULO_MOTIVO[m], contagem[m]) for m in ORDEM_MOTIVO if m in contagem
        ]
        # Quem volta antes; a data é o que o gestor compara com o dia da falta.
        na_folga = sorted(
            (p for p in r.preteridos if p.livre_em is not None),
            key=lambda p: p.livre_em,
        )
        saida.append(FaltaNoDia(
            dia=r.dia, cor=r.cor,
            postos=r.postos_solicitados, escalados=len(r.escolhidos),
            por_motivo=por_motivo,
            liberam_primeiro=[(p.militar, p.livre_em.date()) for p in na_folga[:3]],
        ))
    return saida, len(curtos)


def escala_roda_cor(escala, cor: Cor) -> bool:
    """A escala gera serviço nesta cor? (regra 4.5 — o museu é só-vermelha.)

    Pública porque o painel também precisa: um dia que a escala não roda não é
    buraco de cobertura.
    """
    return escala.tem_preta if cor is Cor.PRETA else escala.tem_vermelha


def escalar_dia(
    session: Session,
    escala_id: int,
    dia: date,
    feriados: set[date] | None = None,
    override_vermelha: set[date] | None = None,
    override_preta: set[date] | None = None,
    escala=None,
) -> ResultadoDia:
    """Calcula (sem gravar) quem serve na `escala_id` no `dia`.

    Calendário e a `Escala` de domínio podem vir prontos (ao escalar um período,
    para não reconsultar/recontar a cada dia); se ausentes, são lidos do banco.
    """
    if escala is None:
        e_orm = session.get(EscalaORM, escala_id)
        if e_orm is None:
            raise ValueError(f"escala {escala_id} não encontrada")
        escala = mapeamento.escala_para_dominio(session, e_orm)

    feriados = feriados if feriados is not None else calendario_service.feriados(session)
    if override_vermelha is None:
        override_vermelha = calendario_service.overrides_vermelha(session)
    if override_preta is None:
        override_preta = calendario_service.overrides_preta(session)
    cor = classificar_dia(dia, feriados, override_vermelha, override_preta)

    # escala que não roda essa cor não gera serviço nesse dia (regra 4.5)
    if not escala.ativa or not escala_roda_cor(escala, cor):
        return ResultadoDia(escala_id, dia, cor, [], escala.postos, efetivo_insuficiente=False)

    parts = mapeamento.participacoes_da_escala(session, escala_id, ate_dia=dia)
    ids = [p.militar.id for p in parts]
    impedimentos = mapeamento.impedimentos_no_dia(session, ids, dia)
    ultimo_termino = mapeamento.ultimo_termino_por_militar(session, escala, ids, antes_de_dia=dia)

    sel = selecionar(escala, parts, cor, dia, impedimentos, ultimo_termino)
    militares = [p.militar for p in sel.escolhidos]
    return ResultadoDia(
        escala_id=escala_id,
        dia=dia,
        cor=cor,
        escolhidos=militares,
        postos_solicitados=escala.postos,
        efetivo_insuficiente=len(militares) < escala.postos,
        preteridos=sel.preteridos,
    )


def gravar_dia(session: Session, resultado: ResultadoDia, escala=None) -> list[Servico]:
    """Persiste os `servico` de um `ResultadoDia`, casando militar -> posto.

    O i-ésimo escolhido (fila) assume o posto de ordem i+1. `inicio_dt`/
    `termino_dt` vêm da janela da escala (regra 2.4). Idempotente: pula postos
    que já têm serviço no dia (constraint uq_servico_posto_dia). `escala` (domínio)
    pode vir pronta para evitar recontar postos/concorrentes.

    Anota em `resultado.servicos_gravados` quantos criou — é o que a tela deve
    anunciar, não o número de escolhidos (que ignora a idempotência).
    """
    if not resultado.escolhidos:
        resultado.servicos_gravados = 0
        return []

    if escala is None:
        escala = mapeamento.escala_para_dominio(session, session.get(EscalaORM, resultado.escala_id))
    postos = session.scalars(
        select(Posto).where(Posto.escala_id == resultado.escala_id).order_by(Posto.ordem)
    ).all()

    inicio_dt = escala.inicio_em(resultado.dia)
    termino_dt = escala.termino_em(resultado.dia)

    ja_gravados = {
        pid for (pid,) in session.execute(
            _servicos_existentes_do_dia(resultado.escala_id, resultado.dia)
        ).all()
    }

    criados: list[Servico] = []
    for militar, posto in zip(resultado.escolhidos, postos):
        if posto.id in ja_gravados:
            continue
        servico = Servico(
            escala_id=resultado.escala_id,
            posto_id=posto.id,
            militar_id=militar.id,
            dia=resultado.dia,
            cor=resultado.cor,
            inicio_dt=inicio_dt,
            termino_dt=termino_dt,
        )
        session.add(servico)
        criados.append(servico)
    session.flush()
    resultado.servicos_gravados = len(criados)
    return criados


def _servicos_existentes_do_dia(escala_id: int, dia: date):
    return (
        select(Servico.posto_id)
        .where(Servico.escala_id == escala_id, Servico.dia == dia)
    )


def escalar_e_gravar_periodo(
    session: Session, escala_id: int, inicio: date, fim: date,
) -> list[ResultadoDia]:
    """Fecha a escala dia a dia de `inicio` a `fim` (inclusive), gravando.

    Cronológico e sequencial de propósito: cada dia gravado alimenta a fila e a
    folga do dia seguinte (regra 6/7). Retorna o resultado de cada dia (o caller
    checa `efetivo_insuficiente` para avisar o gestor — regra 7.8).

    A `Escala` de domínio e o calendário são resolvidos UMA vez para todo o
    período (config da escala é invariante no intervalo).
    """
    if fim < inicio:
        raise ValueError("'fim' anterior a 'inicio'")

    e_orm = session.get(EscalaORM, escala_id)
    if e_orm is None:
        raise ValueError(f"escala {escala_id} não encontrada")
    escala = mapeamento.escala_para_dominio(session, e_orm)

    feriados = calendario_service.feriados(session, inicio, fim)
    override_vermelha = calendario_service.overrides_vermelha(session, inicio, fim)
    override_preta = calendario_service.overrides_preta(session, inicio, fim)

    resultados: list[ResultadoDia] = []
    dia = inicio
    while dia <= fim:
        resultado = escalar_dia(
            session, escala_id, dia, feriados, override_vermelha, override_preta, escala=escala,
        )
        gravar_dia(session, resultado, escala=escala)
        resultados.append(resultado)
        dia += timedelta(days=1)
    return resultados
