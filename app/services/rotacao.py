"""Serviço de rotação: aplica o motor do domínio sobre os dados do banco.

É a orquestração que faltava entre `app/domain/motor.py` (lógica pura) e o
banco: monta as entradas do domínio (via mapeamento), classifica o dia, chama
`proximos` e — opcionalmente — grava os `servico` resultantes.

Nada de regra nova aqui: a regra mora no domínio. Este módulo só busca dados,
chama o motor e persiste o retrato (regra 6/7).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.calendario import classificar_dia
from app.domain.models import Cor, Militar as MilitarDom
from app.domain.motor import proximos
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

    escolhidos = proximos(escala, parts, cor, dia, impedimentos, ultimo_termino)
    militares = [p.militar for p in escolhidos]
    return ResultadoDia(
        escala_id=escala_id,
        dia=dia,
        cor=cor,
        escolhidos=militares,
        postos_solicitados=escala.postos,
        efetivo_insuficiente=len(militares) < escala.postos,
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
