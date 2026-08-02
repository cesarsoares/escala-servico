"""Permutas / trocas (seção 9/10 das regras).

Registro PURO: a permuta apenas anota que outro militar cobriu um serviço. NÃO
há retribuição automática nem recálculo de folga — a folga segue colada em
`servico.militar_id` (o ESCALADO), nunca no substituto (regra 9).

⚠️ **A folga mínima NÃO barra a troca** (regra 10.5 reescrita pelo Brigada em
01/08/2026; a versão anterior negava). Cobrir não conta na folga de quem cobre —
a contagem fica com o substituído (10.2) —, então não há folga a ferir. A
barreira antiga recusava trocas legítimas entre dias PREVISTOS: o João, previsto
para o dia 12, não conseguia assumir o 13 do Paulo, porque o sistema contava
contra ele o serviço do dia 12 que, feita a troca, ele não cumpre.

Continuam barrando, e por serem IMPOSSIBILIDADE e não equidade: substituto
impedido no dia (7.5), substituto já de serviço no mesmo dia em escala
concorrente, substituto igual ao escalado e serviço já permutado.

Isto vale só para a TROCA. Na escalação automática (motor, regra 7.4) a folga
mínima continua valendo integralmente.

⚠️ **Não há trava de DATA, e é deliberado** (confirmado pelo usuário em
01/08/2026, ao testar a troca de um serviço já em andamento). Trocar o escalado
de um serviço que começou hoje é o caso NORMAL, não o caso estranho: o militar
passa mal na parada e outro assume no lugar dele. Serviço passado idem —
registrar o que de fato aconteceu é o objetivo, mesma postura de fato consumado
do lançamento à mão e da importação de CSV.

Quem não mexe no passado é o REAJUSTE automático (`services/reajuste.py`), e são
coisas diferentes: lá o SISTEMA decide sozinho, e reescrever o que já ocorreu
seria falsificação; aqui quem decide é o gestor, registrando um fato.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.escala import Escala as EscalaORM
from app.models.militar import Militar as MilitarORM
from app.models.servico import Permuta, Servico
from app.services import mapeamento


class PermutaNegada(Exception):
    """Permuta recusada por regra de negócio (regra 10.5). Carrega o motivo."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


def registrar_permuta(
    session: Session,
    servico_id: int,
    militar_substituto_id: int,
    autorizado_por: int | None = None,
    observacao: str | None = None,
) -> Permuta:
    """Registra que `militar_substituto_id` cobre o serviço `servico_id`.

    Levanta ValueError se serviço/militar não existem; PermutaNegada se a troca
    esbarra numa impossibilidade (substituto = escalado, serviço já permutado,
    substituto impedido no dia ou já de serviço nesse dia). Folga mínima não
    entra mais aqui (10.5). A folga NÃO é recalculada (regra 9).
    """
    servico = session.get(Servico, servico_id)
    if servico is None:
        raise ValueError(f"serviço {servico_id} não encontrado")

    substituto = session.get(MilitarORM, militar_substituto_id)
    if substituto is None:
        raise ValueError(f"militar {militar_substituto_id} não encontrado")

    if servico.militar_id == militar_substituto_id:
        raise PermutaNegada("o substituto é o próprio militar escalado")

    # consulta direta (não a relação servico.permuta, que pode estar em cache)
    ja_existe = session.scalar(select(Permuta).where(Permuta.servico_id == servico_id))
    if ja_existe is not None:
        raise PermutaNegada("este serviço já possui permuta registrada")

    _garantir_disponibilidade(session, servico, militar_substituto_id)

    permuta = Permuta(
        servico_id=servico_id,
        militar_substituto_id=militar_substituto_id,
        autorizado_por=autorizado_por,
        observacao=observacao,
    )
    session.add(permuta)
    session.flush()
    return permuta


def cancelar_permuta(session: Session, servico_id: int) -> bool:
    """Remove a permuta de um serviço (o escalado volta a figurar). Idempotente."""
    permuta = session.scalar(select(Permuta).where(Permuta.servico_id == servico_id))
    if permuta is None:
        return False
    session.delete(permuta)
    session.flush()
    return True


def _garantir_disponibilidade(session: Session, servico: Servico, substituto_id: int) -> None:
    """Aplica ao substituto as guardas que sobraram (regras 7.5 e 10.5).

    Só IMPOSSIBILIDADE entra aqui. A folga mínima saiu em 01/08/2026 (ver o
    docstring do módulo): cobrir não conta na folga de quem cobre, então não há
    folga a ferir. Quem julga o descanso de quem se ofereceu para cobrir é o
    gestor que autoriza a troca.
    """
    e_orm = session.get(EscalaORM, servico.escala_id)
    escala = mapeamento.escala_para_dominio(session, e_orm)

    impedimentos = mapeamento.impedimentos_no_dia(session, [substituto_id], servico.dia)
    if impedimentos:
        raise PermutaNegada("o substituto está impedido no dia do serviço")

    # Dois serviços de 24h no mesmo dia é impossível, não é questão de folga:
    # a pessoa não está em dois lugares. Vale na escala e nas concorrentes.
    relevantes = {escala.id, *escala.concorrentes}
    ja_serve_hoje = session.scalar(
        select(Servico.id).where(
            Servico.militar_id == substituto_id,
            Servico.dia == servico.dia,
            Servico.escala_id.in_(relevantes),
            Servico.id != servico.id,
        )
    )
    if ja_serve_hoje is not None:
        raise PermutaNegada("o substituto já está de serviço nesse dia")
