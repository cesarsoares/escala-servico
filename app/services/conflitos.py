"""Conflito entre serviço já gravado e impedimento lançado depois (regra 7.5).

O motor nunca escala quem está impedido — isso é do domínio e está testado. O
que a OM vive é outra coisa: o mês é fechado, e SÓ ENTÃO chega a dispensa. O que
está gravado envelheceu, e `gravar_dia` é idempotente (um período já fechado
grava zero), então re-escalar não desfaz nada.

Até aqui a única saída era **regravar** — apagar o período inteiro e refazer.
Isso resolve, mas com um custo que a escala publicada não suporta: um
impedimento de três dias muda quem serve nos trinta, e as permutas do período
vão junto por CASCADE. Este módulo é a saída pontual: **troca o impedido pelo
próximo da fila naquele dia**, e não toca em mais nada.

O preço, dito aqui para não se descobrir depois: a substituição pontual NÃO
recalcula os dias seguintes, então a rotação fica levemente fora do ciclo que o
motor produziria do zero. É deliberado — é o que o Brigada faz à mão, e mantém
publicável o que já foi publicado. Quem quiser o ciclo perfeito continua tendo o
'regravar'.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.folga import folga_efetiva_horas, respeita_folga_minima
from app.domain.models import Cor, Militar as MilitarDom
from app.domain.motor import disponivel, fila_ordenada
from app.models.escala import Escala as EscalaORM, Participacao, Posto
from app.models.impedimento import Impedimento as ImpedimentoORM
from app.models.militar import Militar as MilitarORM
from app.models.referencia import PostoGraduacao, TipoImpedimento
from app.models.servico import Permuta, Servico
from app.services import mapeamento


@dataclass
class Conflito:
    """Um serviço gravado cujo escalado está impedido naquele dia."""
    servico_id: int
    escala_id: int
    escala_nome: str
    dia: date
    cor: Cor
    posto_rotulo: str
    militar_id: int
    militar: str                    # "3º Sgt SILVA", como sai no documento
    impedimento_inicio: date
    impedimento_fim: date
    impedimento_tipo: str
    # A permuta muda o quadro: se alguém já cobre o serviço, o impedido não vai
    # servir de qualquer forma (regra 9). Continua sendo conflito — a folga é do
    # escalado —, mas não é urgência, e substituí-lo apagaria o registro da troca.
    tem_permuta: bool = False
    substituto: MilitarDom | None = None
    motivo_sem_substituto: str | None = None


def conflitos(
    db: Session,
    *,
    militar_id: int | None = None,
    escala_id: int | None = None,
    de: date | None = None,
    ate: date | None = None,
    com_substituto: bool = True,
) -> list[Conflito]:
    """Serviços gravados cujo escalado está impedido no dia (regra 7.5).

    Sem filtro de data varre tudo — é o que a tela do impedimento recém-gravado
    quer (o período do impedimento é o recorte). `com_substituto=False` pula a
    consulta do candidato, para quem só precisa contar.
    """
    consulta = (
        select(Servico, EscalaORM.nome, ImpedimentoORM, TipoImpedimento.nome,
               MilitarORM.nome_guerra, PostoGraduacao.sigla, Posto.rotulo)
        .join(EscalaORM, EscalaORM.id == Servico.escala_id)
        .join(MilitarORM, MilitarORM.id == Servico.militar_id)
        .join(PostoGraduacao, PostoGraduacao.id == MilitarORM.posto_graduacao_id)
        .join(Posto, Posto.id == Servico.posto_id)
        .join(ImpedimentoORM, ImpedimentoORM.militar_id == Servico.militar_id)
        .join(TipoImpedimento, TipoImpedimento.id == ImpedimentoORM.tipo_impedimento_id)
        .where(ImpedimentoORM.inicio <= Servico.dia, Servico.dia <= ImpedimentoORM.fim)
        .order_by(Servico.dia, EscalaORM.nome, Posto.ordem)
    )
    if militar_id is not None:
        consulta = consulta.where(Servico.militar_id == militar_id)
    if escala_id is not None:
        consulta = consulta.where(Servico.escala_id == escala_id)
    if de is not None:
        consulta = consulta.where(Servico.dia >= de)
    if ate is not None:
        consulta = consulta.where(Servico.dia <= ate)

    linhas = db.execute(consulta).all()
    if not linhas:
        return []

    permutados = {
        sid for (sid,) in db.execute(
            select(Permuta.servico_id).where(
                Permuta.servico_id.in_([s.id for s, *_ in linhas]))
        ).all()
    }

    saida: list[Conflito] = []
    for servico, escala_nome, imp, tipo_nome, guerra, sigla, rotulo in linhas:
        c = Conflito(
            servico_id=servico.id,
            escala_id=servico.escala_id,
            escala_nome=escala_nome,
            dia=servico.dia,
            cor=servico.cor,
            posto_rotulo=rotulo or "Posto",
            militar_id=servico.militar_id,
            militar=f"{sigla} {guerra}",
            impedimento_inicio=imp.inicio,
            impedimento_fim=imp.fim,
            impedimento_tipo=tipo_nome,
            tem_permuta=servico.id in permutados,
        )
        if com_substituto:
            c.substituto, c.motivo_sem_substituto = candidato(db, servico)
        saida.append(c)
    return saida


def candidato(db: Session, servico: Servico) -> tuple[MilitarDom | None, str | None]:
    """Quem o motor colocaria no lugar do escalado, NESTE dia (regra 6.1).

    Mesma fila e mesmas guardas da escalação normal — mais duas que só existem
    aqui, porque a substituição é retroativa e o resto do mês já está gravado:

      - quem já serve nesse dia (nesta escala ou numa concorrente) não pode
        dobrar; a folga olha `dia <` e não pegaria isso;
      - **o serviço seguinte do candidato já existe**. Na escalação cronológica o
        futuro ainda não foi escrito, então ninguém precisou olhar para frente.
        Aqui, colocar alguém na véspera do serviço que ele já tem fere a folga
        dele — a regra 7.4 vale nos dois sentidos.

    Retorna (militar, None) ou (None, motivo) — o motivo vai para a tela: "não há
    substituto" sem explicação é a mensagem que faz o gestor desconfiar do sistema.
    """
    e_orm = db.get(EscalaORM, servico.escala_id)
    if e_orm is None:                                    # pragma: no cover - FK garante
        return None, "escala não encontrada"
    escala = mapeamento.escala_para_dominio(db, e_orm)

    parts = mapeamento.participacoes_da_escala(db, servico.escala_id, ate_dia=servico.dia)
    ids = [p.militar.id for p in parts]
    impedimentos = mapeamento.impedimentos_no_dia(db, ids, servico.dia)
    ultimo_termino = mapeamento.ultimo_termino_por_militar(
        db, escala, ids, antes_de_dia=servico.dia)

    relevantes = {escala.id, *escala.concorrentes}
    ja_servem_hoje = {
        mid for (mid,) in db.execute(
            select(Servico.militar_id).where(
                Servico.dia == servico.dia,
                Servico.escala_id.in_(relevantes),
                Servico.id != servico.id,
            )
        ).all()
    }

    fila = fila_ordenada(parts, servico.cor)
    if not fila:
        return None, "nenhum participante ativo concorre nesta cor"

    havia_algum = False
    for p in fila:
        mid = p.militar.id
        if mid == servico.militar_id or mid in ja_servem_hoje:
            continue
        havia_algum = True
        if not disponivel(p, servico.dia, impedimentos, ultimo_termino.get(mid),
                          servico.inicio_dt, escala.folga_minima_horas):
            continue
        if _fere_servico_futuro(db, escala, mid, servico):
            continue
        return p.militar, None

    if not havia_algum:
        return None, "não há outro participante disponível nesta escala"
    return None, "todos os demais estão impedidos ou sem a folga mínima no dia"


def _fere_servico_futuro(db: Session, escala, militar_id: int, servico: Servico) -> bool:
    """O candidato já tem serviço perto demais DEPOIS deste? (regra 7.4)

    A folga é medida do término deste serviço ao início do próximo — e o próximo
    pode pertencer a outra escala concorrente, com folga própria: o piso que vale
    é o da escala de DESTINO (regra 7.4.2), que aqui é a do serviço futuro.
    """
    relevantes = {escala.id, *escala.concorrentes}
    # Nenhuma folga configurável passa de poucos dias; a janela limita a varredura.
    limite = servico.dia + timedelta(days=_dias_de_folga_max(db, relevantes))
    futuros = db.execute(
        select(Servico.inicio_dt, EscalaORM.folga_minima_horas)
        .join(EscalaORM, EscalaORM.id == Servico.escala_id)
        .where(
            Servico.militar_id == militar_id,
            Servico.escala_id.in_(relevantes),
            Servico.dia > servico.dia,
            Servico.dia <= limite,
        )
    ).all()
    return any(
        not respeita_folga_minima(servico.termino_dt, inicio_dt, folga)
        for inicio_dt, folga in futuros
    )


def _dias_de_folga_max(db: Session, escala_ids: set[int]) -> int:
    """Quantos dias à frente vale olhar: a maior folga em jogo, arredondada."""
    horas = [
        h for (h,) in db.execute(
            select(EscalaORM.folga_minima_horas).where(EscalaORM.id.in_(escala_ids))
        ).all()
    ]
    maior = max((folga_efetiva_horas(h) for h in horas), default=folga_efetiva_horas(None))
    return maior // 24 + 2      # + a duração do serviço, + a borda


class SubstituicaoNegada(Exception):
    """Substituição recusada por regra de negócio. Carrega o motivo."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


def substituir(db: Session, servico_id: int, novo_militar_id: int) -> Servico:
    """Troca o escalado de um serviço, mantendo posto, dia, cor e janela.

    ALTERA a linha em vez de apagar e recriar: o `servico.id` é o que a permuta
    referencia e o que a auditoria já citou. Também mantém a vaga preenchida —
    apagar primeiro deixaria o dia descoberto entre uma coisa e outra.

    Não escolhe por conta própria: quem escolhe é `candidato`, e a tela mostra a
    escolha antes de gravar. Mas revalida tudo aqui — a tela pode ter sido
    montada há dez minutos, e a URL pode trazer qualquer id.
    """
    servico = db.get(Servico, servico_id)
    if servico is None:
        raise ValueError(f"serviço {servico_id} não encontrado")
    if novo_militar_id == servico.militar_id:
        raise SubstituicaoNegada("o substituto é o próprio militar escalado")
    if db.get(MilitarORM, novo_militar_id) is None:
        raise ValueError(f"militar {novo_militar_id} não encontrado")

    permuta = db.scalar(select(Permuta).where(Permuta.servico_id == servico_id))
    if permuta is not None:
        raise SubstituicaoNegada(
            "este serviço tem permuta registrada; cancele a permuta antes de "
            "trocar o escalado"
        )

    _garantir_elegivel(db, servico, novo_militar_id)
    servico.militar_id = novo_militar_id
    db.flush()
    return servico


def _garantir_elegivel(db: Session, servico: Servico, militar_id: int) -> None:
    """As guardas de `candidato`, aplicadas a um militar escolhido de fora.

    O gestor pode indicar outro nome que não o proposto — pode ter motivo que o
    sistema não conhece. O que ele NÃO pode é criar um serviço que a regra proíbe:
    impedido no dia, sem folga, ou dobrando com outro serviço.
    """
    e_orm = db.get(EscalaORM, servico.escala_id)
    escala = mapeamento.escala_para_dominio(db, e_orm)

    participa = db.execute(
        select(Participacao.serve_preta, Participacao.serve_vermelha).where(
            Participacao.escala_id == servico.escala_id,
            Participacao.militar_id == militar_id,
            Participacao.ativo.is_(True),
        )
    ).first()
    if participa is None:
        raise SubstituicaoNegada("o militar não é participante ativo desta escala")
    # regra 3.3.1: quem só concorre numa cor não pode ser posto na outra nem
    # pela mão do gestor — a restrição costuma vir da função que ele exerce.
    serve_preta, serve_vermelha = participa
    if not (serve_preta if servico.cor is Cor.PRETA else serve_vermelha):
        raise SubstituicaoNegada(
            f"o militar não concorre na escala {servico.cor.value} (regra 3.3.1)")

    if mapeamento.impedimentos_no_dia(db, [militar_id], servico.dia):
        raise SubstituicaoNegada("o substituto está impedido no dia do serviço")

    relevantes = {escala.id, *escala.concorrentes}
    dobra = db.scalar(
        select(Servico.id).where(
            Servico.militar_id == militar_id,
            Servico.dia == servico.dia,
            Servico.escala_id.in_(relevantes),
            Servico.id != servico.id,
        )
    )
    if dobra is not None:
        raise SubstituicaoNegada("o substituto já está de serviço nesse dia")

    # ⚠️ A folga mínima CONTINUA valendo aqui, e não é contradição com a 10.5
    # (que a tirou da permuta em 01/08/2026): lá o escalado não muda e a folga
    # fica com ele — quem cobre não ganha nada e não deve nada. Aqui o substituto
    # PASSA A SER o escalado do dia: ganha a folga, entra na fila por este
    # serviço, e sai no documento como responsável. É escalação, não registro.
    ultimo = mapeamento.ultimo_termino_por_militar(
        db, escala, [militar_id], antes_de_dia=servico.dia).get(militar_id)
    if not respeita_folga_minima(ultimo, servico.inicio_dt, escala.folga_minima_horas):
        raise SubstituicaoNegada("a troca feriria a folga mínima do substituto (regra 7.4)")

    if _fere_servico_futuro(db, escala, militar_id, servico):
        raise SubstituicaoNegada(
            "o substituto já tem serviço logo depois deste, sem a folga mínima "
            "entre os dois (regra 7.4)"
        )


def descobrir(db: Session, servico_id: int) -> Servico:
    """Apaga o serviço, deixando a vaga vazia (regra 7.8).

    A saída para quando não há substituto: o dia fica com menos militares que
    postos, o painel acusa, e o gestor decide (regra 8). É melhor que o documento
    publicado anunciar um militar que está de férias.
    """
    servico = db.get(Servico, servico_id)
    if servico is None:
        raise ValueError(f"serviço {servico_id} não encontrado")
    db.delete(servico)
    db.flush()
    return servico


def retrato(servico: Servico) -> dict:
    """Retrato do serviço para a auditoria (regra 11)."""
    return {
        "servico_id": servico.id,
        "escala_id": servico.escala_id,
        "posto_id": servico.posto_id,
        "militar_id": servico.militar_id,
        "dia": servico.dia.isoformat(),
        "cor": servico.cor.value,
    }
