"""Lançamento MANUAL de serviço (demanda do Brigada, 30/07).

O pedido, nas palavras dele: "poder alterar a data em que o militar tirou o
último serviço; o sistema deve considerar essa data para contagem de folga. Se
tiver que manusear a escala sem passar pelo sistema, em caso de emergência,
poderá ser feito um registro no sistema."

**Não existe campo de "último serviço", e não vai existir.** A fila (regra 6.2) e
a folga (regra 7.4) são DERIVADAS da tabela `servico`: `mapeamento` calcula
max(dia) por cor e max(termino_dt) por militar. Criar uma coluna paralela de
"última data" seria um segundo lugar onde a mesma verdade mora — e dois lugares
divergem. O que este módulo faz é deixar o gestor **escrever na própria tabela**:
lançar o serviço que aconteceu fora do sistema, corrigir a data de um que está
errado, apagar o que não houve. A fila e a folga passam a considerá-lo sozinhas,
sem uma linha de código nova no motor.

**Postura: fato consumado, como na importação de CSV.** O que o gestor registra
já aconteceu — recusar porque hoje feriria a folga mínima impediria de registrar
exatamente o que se quer registrar. Então há duas listas:

  - `erros` IMPEDEM (o banco não aceitaria, ou o registro seria incoerente):
    vaga já ocupada naquele dia, militar/escala/posto inexistentes, o militar
    dobrando com outro serviço do mesmo dia;
  - `avisos` DEIXAM PASSAR mas aparecem antes de gravar: folga curta, militar
    que não é mais participante, cor em que ele não concorre, data no futuro.

Quem decide é o gestor, na segunda etapa — o mesmo conferir → confirmar da ficha
em PDF, do CSV e da restauração de backup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.calendario import classificar_dia
from app.domain.folga import respeita_folga_minima
from app.domain.models import Cor
from app.models.escala import Escala as EscalaORM, Participacao, Posto
from app.models.militar import Militar as MilitarORM
from app.models.servico import Permuta, Servico
from app.services import calendario_service, mapeamento


@dataclass
class Analise:
    """O que vai acontecer, com o que impede e o que apenas preocupa."""
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    # resolvidos quando não há erro de existência
    escala_nome: str = ""
    posto_rotulo: str = ""
    militar_nome: str = ""
    dia: date | None = None
    cor: Cor | None = None
    inicio_dt: datetime | None = None
    termino_dt: datetime | None = None

    @property
    def pode_gravar(self) -> bool:
        return not self.erros


class LancamentoNegado(Exception):
    """Gravação recusada por erro que impede (carrega os motivos)."""

    def __init__(self, motivos: list[str]):
        super().__init__("; ".join(motivos))
        self.motivos = motivos


def cor_do_dia(db: Session, dia: date) -> Cor:
    """A cor sai do CALENDÁRIO, nunca de quem digita (regra 5).

    Mesma decisão da importação de CSV: cor é consequência da data — feriados e
    overrides da OM inclusive. Deixar o operador escolher permitiria registrar
    sábado como preta, e a fila da cor errada envenena a rotação inteira.
    """
    return classificar_dia(
        dia,
        calendario_service.feriados(db, dia, dia),
        calendario_service.overrides_vermelha(db, dia, dia),
        calendario_service.overrides_preta(db, dia, dia),
    )


def analisar(
    db: Session,
    escala_id: int,
    posto_id: int,
    dia: date,
    militar_id: int,
    hoje: date | None = None,
    ignorar_servico_id: int | None = None,
) -> Analise:
    """Confere um lançamento SEM gravar nada (etapa 1).

    `ignorar_servico_id` é o serviço que está sendo editado: sem ele, alterar
    apenas o militar acusaria "vaga já ocupada" pela própria linha em edição.
    """
    a = Analise(dia=dia)
    hoje = hoje or date.today()

    e_orm = db.get(EscalaORM, escala_id)
    posto = db.get(Posto, posto_id)
    militar = db.scalar(select(MilitarORM).where(MilitarORM.id == militar_id))
    if e_orm is None:
        a.erros.append("Escala não encontrada.")
    if posto is None:
        a.erros.append("Posto não encontrado.")
    elif e_orm is not None and posto.escala_id != escala_id:
        a.erros.append("O posto não pertence a esta escala.")
    if militar is None:
        a.erros.append("Militar não encontrado.")
    if a.erros:
        return a

    escala = mapeamento.escala_para_dominio(db, e_orm)
    a.escala_nome = e_orm.nome
    a.posto_rotulo = posto.rotulo or f"Posto {posto.ordem}"
    a.militar_nome = militar.nome_guerra
    a.cor = cor_do_dia(db, dia)
    a.inicio_dt = escala.inicio_em(dia)
    a.termino_dt = escala.termino_em(dia)

    # --- erros: o registro seria incoerente ---
    ocupada = db.scalar(
        select(Servico.id).where(
            Servico.posto_id == posto_id, Servico.dia == dia,
            Servico.id != (ignorar_servico_id or -1),
        )
    )
    if ocupada is not None:
        a.erros.append(
            "Esta vaga já tem serviço neste dia. Altere ou remova o que está lá.")

    relevantes = {escala.id, *escala.concorrentes}
    dobra = db.scalar(
        select(Servico.id).where(
            Servico.militar_id == militar_id, Servico.dia == dia,
            Servico.escala_id.in_(relevantes),
            Servico.id != (ignorar_servico_id or -1),
        )
    )
    if dobra is not None:
        a.erros.append(
            f"{militar.nome_guerra} já está de serviço neste dia, nesta escala ou "
            "numa concorrente — seriam dois turnos ao mesmo tempo.")

    # --- avisos: fato consumado, mas o gestor precisa ver ---
    part = db.execute(
        select(Participacao.ativo, Participacao.serve_preta, Participacao.serve_vermelha)
        .where(Participacao.escala_id == escala_id,
               Participacao.militar_id == militar_id)
    ).first()
    if part is None:
        a.avisos.append(
            f"{militar.nome_guerra} não é participante desta escala. O serviço "
            "entra assim mesmo e conta para a folga dele.")
    else:
        ativo, serve_preta, serve_vermelha = part
        if not ativo:
            a.avisos.append(
                f"{militar.nome_guerra} está isento desta escala (regra 7.6). "
                "O serviço entra assim mesmo.")
        elif not (serve_preta if a.cor is Cor.PRETA else serve_vermelha):
            a.avisos.append(
                f"{militar.nome_guerra} não concorre na escala {a.cor.value} "
                "(regra 3.3.1). Costuma indicar dia ou militar trocado.")

    if mapeamento.impedimentos_no_dia(db, [militar_id], dia):
        a.avisos.append(
            f"{militar.nome_guerra} tem impedimento registrado neste dia (regra 7.5).")

    ultimo = mapeamento.ultimo_termino_por_militar(
        db, escala, [militar_id], antes_de_dia=dia).get(militar_id)
    if not respeita_folga_minima(ultimo, a.inicio_dt, escala.folga_minima_horas):
        a.avisos.append(
            f"Folga menor que o mínimo da escala ({escala.folga_horas()}h): o "
            f"serviço anterior terminou em {ultimo:%d/%m/%Y %H:%M}.")

    if dia > hoje:
        a.avisos.append(
            "A data está no futuro. Lançamento manual é para registrar o que já "
            "aconteceu — para o que vem, use Escalar período.")
    return a


def lancar(db: Session, escala_id: int, posto_id: int, dia: date, militar_id: int) -> Servico:
    """Grava o serviço lançado à mão (etapa 2). Revalida antes.

    A releitura é deliberada, como na importação de CSV: entre conferir e
    confirmar, alguém pode ter fechado o dia pela tela de escalação.
    """
    a = analisar(db, escala_id, posto_id, dia, militar_id)
    if not a.pode_gravar:
        raise LancamentoNegado(a.erros)

    servico = Servico(
        escala_id=escala_id, posto_id=posto_id, militar_id=militar_id,
        dia=dia, cor=a.cor, inicio_dt=a.inicio_dt, termino_dt=a.termino_dt,
    )
    db.add(servico)
    db.flush()
    return servico


def alterar(
    db: Session, servico_id: int, dia: date, militar_id: int, posto_id: int | None = None,
) -> Servico:
    """Corrige um serviço: a data, quem serviu, ou os dois.

    É o "alterar a data do último serviço" do pedido. Mudar o dia **recalcula a
    cor e a janela**: um serviço movido de sexta para sábado é vermelho, e sua
    folga é medida a partir de outro instante. Guardar a cor antiga porque a
    linha já existia poria o militar na fila errada.
    """
    servico = db.get(Servico, servico_id)
    if servico is None:
        raise ValueError(f"serviço {servico_id} não encontrado")

    permuta = db.scalar(select(Permuta).where(Permuta.servico_id == servico_id))
    if permuta is not None:
        raise LancamentoNegado([
            "Este serviço tem permuta registrada; cancele a permuta antes de "
            "alterá-lo — ela aponta para este dia e este escalado (regra 9)."
        ])

    posto_id = posto_id or servico.posto_id
    a = analisar(db, servico.escala_id, posto_id, dia, militar_id,
                 ignorar_servico_id=servico_id)
    if not a.pode_gravar:
        raise LancamentoNegado(a.erros)

    servico.posto_id = posto_id
    servico.militar_id = militar_id
    servico.dia = dia
    servico.cor = a.cor
    servico.inicio_dt = a.inicio_dt
    servico.termino_dt = a.termino_dt
    db.flush()
    return servico


def remover(db: Session, servico_id: int) -> Servico:
    """Apaga um serviço lançado por engano. A permuta vai junto (CASCADE)."""
    servico = db.get(Servico, servico_id)
    if servico is None:
        raise ValueError(f"serviço {servico_id} não encontrado")
    db.delete(servico)
    db.flush()
    return servico


def retrato(servico: Servico) -> dict:
    """Retrato para a auditoria (regra 11)."""
    return {
        "servico_id": servico.id,
        "escala_id": servico.escala_id,
        "posto_id": servico.posto_id,
        "militar_id": servico.militar_id,
        "dia": servico.dia.isoformat(),
        "cor": servico.cor.value,
        "inicio_dt": servico.inicio_dt.isoformat(),
        "termino_dt": servico.termino_dt.isoformat(),
    }
