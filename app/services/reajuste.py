"""Reajuste automático da escala a partir de um dia (demanda do Brigada, 01/08).

O pedido, nas palavras dele: *"sempre que houver alteração na escala (permuta,
dispensa, etc) o sistema deverá disparar o processo de ajustar a escala, sem a
necessidade do brigada 'rodar' a escala novamente. A escala deve ser ajustada do
dia em questão para a frente"*.

Em 30/07 a objeção a isto era outra: refazer o mês muda quem serve em todos os
dias, e o mês já podia ter saído no boletim — foi por isso que nasceu a tela de
Conflitos, que troca um dia só. **A objeção caiu quando ele explicou o que o
boletim é** (01/08): a previsão que o sistema publica serve para as pessoas se
planejarem; o documento OFICIAL é o boletim, e ele cobre o **dia seguinte** — na
quinta-feira, o bloco de sexta a segunda. Refazer de um dia em diante, portanto,
não invalida nada publicado, contanto que o gestor SAIBA quando alcança a janela
que já foi ao boletim. Daí `dias_no_boletim`.

Quatro decisões que o código sozinho não deixaria evidentes:

1. **Permuta não é refeita.** O dia que tem permuta registrada fica intocado, e
   o reajuste segue nos demais. A permuta é um acerto entre duas pessoas,
   autorizado pelo gestor: o sistema não a desfaz sozinho — e o `regravar`
   manual, que a apaga por CASCADE, é outra coisa, ali o gestor pediu. (Decisão
   do usuário, 01/08; o achado 1 do code-review de 25/07 é o histórico disso.)
2. **Nada antes de amanhã.** O serviço de hoje já começou; o de ontem acabou.
   Reescrever o passado não é ajuste, é falsificação — para o dia corrente e o
   passado existe a tela de Conflitos (troca pontual) e o lançamento à mão.
3. **O horizonte é o que já está fechado.** Reajustar não estende a escala: vai
   até o último dia gravado. Fechar mês novo continua sendo ato do gestor, em
   Escalar.
4. **Permuta NÃO dispara reajuste** (quem chama é que decide, mas está dito
   aqui porque é onde se procura): ela não mexe na fila — a folga continua com o
   escalado (regra 9) —, então refazer os dias seguintes mudaria a escala sem
   que nada tivesse mudado.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload

from app.models.escala import Escala as EscalaORM, Participacao, Posto
from app.models.militar import Militar as MilitarORM
from app.models.servico import Permuta, Servico
from app.services import auditoria as auditoria_service
from app.services import calendario_service, mapeamento, rotacao

# Como o reajuste aparece na auditoria (regra 11) e, por tabela, na tela que o
# reconstitui. Ficam aqui, e não no router, porque o router da tela precisa
# importar `gestor_web` de `web/gestao.py` — que por sua vez dispara reajuste.
ENTIDADE = "reajuste"
ACAO = "reajustar"


@dataclass
class DiaReajustado:
    """Um dia cujo quadro mudou, com o antes e o depois já legíveis."""
    dia: date
    antes: list[str]
    depois: list[str]
    no_boletim: bool = False       # cai na janela que já foi publicada


@dataclass
class Reajuste:
    """O que o reajuste fez numa escala."""
    escala_id: int
    escala_nome: str
    inicio: date | None = None
    fim: date | None = None
    dias_alterados: list[DiaReajustado] = field(default_factory=list)
    dias_processados: int = 0
    pulados_por_permuta: list[date] = field(default_factory=list)
    # Dias que ficaram com menos militares que postos DEPOIS do reajuste (7.8).
    dias_descobertos: list[date] = field(default_factory=list)

    @property
    def mudou(self) -> bool:
        return bool(self.dias_alterados)

    @property
    def dias_no_boletim(self) -> list[DiaReajustado]:
        return [d for d in self.dias_alterados if d.no_boletim]

    def resumo(self) -> dict:
        """Retrato para a auditoria (regra 11) e para a tela reconstituir depois.

        A tela não pode recalcular o "antes": depois de gravar, ele não existe
        mais em lugar nenhum. Ou fica aqui, ou some.
        """
        return {
            "escala_id": self.escala_id,
            "escala": self.escala_nome,
            "inicio": self.inicio.isoformat() if self.inicio else None,
            "fim": self.fim.isoformat() if self.fim else None,
            "dias_processados": self.dias_processados,
            "alterados": [
                {"dia": d.dia.isoformat(), "antes": d.antes, "depois": d.depois,
                 "no_boletim": d.no_boletim}
                for d in self.dias_alterados
            ],
            "pulados_por_permuta": [d.isoformat() for d in self.pulados_por_permuta],
            "descobertos": [d.isoformat() for d in self.dias_descobertos],
        }


def dias_no_boletim(hoje: date | None = None) -> set[date]:
    """Os dias cujo serviço já saiu no boletim — o que o gestor precisa saber.

    Prática da OM, dita pelo Brigada em 01/08: o boletim publica o serviço do
    **dia seguinte**; na **quinta-feira** publica o bloco **sexta, sábado,
    domingo e segunda**, porque não há expediente no fim de semana.

    É um AVISO, não uma trava: o reajuste acontece de qualquer jeito, e o gestor
    é quem decide se aquilo vira aditamento. Por isso a aproximação (sábado e
    domingo, sem expediente, não publicam nada) é aceitável — errar para menos
    aqui só deixa de destacar uma linha; errar para mais travaria a escala.
    """
    hoje = hoje or date.today()
    if hoje.weekday() == 3:                      # quinta
        # sexta, sábado, domingo e segunda
        return {hoje + timedelta(days=n) for n in range(1, 5)}
    if hoje.weekday() in (5, 6):                 # sáb/dom: nada foi publicado hoje
        return set()
    return {hoje + timedelta(days=1)}


def _quadro_do_dia(db: Session, escala_id: int, inicio: date, fim: date) -> dict[date, list[str]]:
    """Quem está gravado em cada dia, na ordem dos postos: 'Cap SILVA'."""
    linhas = db.execute(
        select(Servico.dia, Posto.ordem, MilitarORM)
        .join(Posto, Posto.id == Servico.posto_id)
        .join(MilitarORM, MilitarORM.id == Servico.militar_id)
        .options(joinedload(MilitarORM.posto_graduacao))
        .where(Servico.escala_id == escala_id, Servico.dia >= inicio, Servico.dia <= fim)
        .order_by(Servico.dia, Posto.ordem)
    ).all()
    quadro: dict[date, list[str]] = {}
    for dia, _ordem, militar in linhas:
        quadro.setdefault(dia, []).append(
            f"{militar.posto_graduacao.sigla} {militar.nome_guerra}")
    return quadro


def _dias_com_permuta(db: Session, escala_id: int, inicio: date, fim: date) -> set[date]:
    return {
        d for (d,) in db.execute(
            select(Servico.dia)
            .join(Permuta, Permuta.servico_id == Servico.id)
            .where(Servico.escala_id == escala_id,
                   Servico.dia >= inicio, Servico.dia <= fim)
        ).all()
    }


def _primeiro_dia_tocavel(a_partir_de: date, hoje: date | None = None) -> date:
    """Nunca antes de amanhã (decisão 2 do módulo)."""
    amanha = (hoje or date.today()) + timedelta(days=1)
    return max(a_partir_de, amanha)


def reajustar(
    db: Session, escala_id: int, a_partir_de: date, hoje: date | None = None,
) -> Reajuste:
    """Refaz a escala de `a_partir_de` (ou de amanhã) até o último dia fechado.

    Sequencial e cronológico, como a escalação normal: cada dia gravado alimenta
    a fila e a folga do seguinte (regra 6/7). Não estende a escala — o horizonte
    é o último dia que já estava gravado.

    Não faz commit: quem chamou está no meio da própria transação (a dispensa
    que disparou isto ainda precisa ser gravada junto, ou nada).
    """
    e_orm = db.get(EscalaORM, escala_id)
    if e_orm is None:
        raise ValueError(f"escala {escala_id} não encontrada")
    r = Reajuste(escala_id=escala_id, escala_nome=e_orm.nome)

    inicio = _primeiro_dia_tocavel(a_partir_de, hoje)
    fim = db.scalar(
        select(func.max(Servico.dia))
        .where(Servico.escala_id == escala_id, Servico.dia >= inicio)
    )
    if fim is None:                     # nada fechado daqui para frente
        return r
    r.inicio, r.fim = inicio, fim

    antes = _quadro_do_dia(db, escala_id, inicio, fim)
    intocaveis = _dias_com_permuta(db, escala_id, inicio, fim)
    r.pulados_por_permuta = sorted(intocaveis)

    # Apaga só o que vai ser refeito. O dia com permuta fica inteiro de pé: além
    # da decisão de negócio, refazer PARTE de um dia abriria a porta para o
    # mesmo militar assumir um segundo posto no mesmo dia (a folga olha
    # `dia <`, e não veria o serviço que ele já tem naquele dia).
    db.execute(delete(Servico).where(
        Servico.escala_id == escala_id, Servico.dia >= inicio, Servico.dia <= fim,
        Servico.dia.notin_(intocaveis) if intocaveis else True,
    ))
    db.flush()
    # ⚠️ DELETE em massa não passa pela sessão: os `Servico` já carregados
    # continuam no mapa de identidade, e o SQLite reaproveita rowid — a linha
    # nova nasce com um id que a sessão julga conhecer. Sem isto, quem chamou
    # (a rota que gravou a dispensa) pode ler o objeto ANTIGO depois do commit.
    db.expire_all()

    escala = mapeamento.escala_para_dominio(db, e_orm)
    feriados = calendario_service.feriados(db, inicio, fim)
    ov_verm = calendario_service.overrides_vermelha(db, inicio, fim)
    ov_preta = calendario_service.overrides_preta(db, inicio, fim)

    dia = inicio
    while dia <= fim:
        if dia in intocaveis:
            dia += timedelta(days=1)
            continue
        resultado = rotacao.escalar_dia(
            db, escala_id, dia, feriados, ov_verm, ov_preta, escala=escala)
        rotacao.gravar_dia(db, resultado, escala=escala)
        r.dias_processados += 1
        if resultado.efetivo_insuficiente and rotacao.escala_roda_cor(escala, resultado.cor):
            r.dias_descobertos.append(dia)
        dia += timedelta(days=1)
    db.flush()

    depois = _quadro_do_dia(db, escala_id, inicio, fim)
    publicados = dias_no_boletim(hoje)
    for d in sorted(set(antes) | set(depois)):
        if d in intocaveis:
            continue
        de, para = antes.get(d, []), depois.get(d, [])
        if de != para:
            r.dias_alterados.append(
                DiaReajustado(dia=d, antes=de, depois=para, no_boletim=d in publicados))
    return r


def registrar_auditoria(
    db: Session, *, gestor_id: int | None, reajustes: list[Reajuste], origem: str,
) -> int | None:
    """Guarda o retrato do reajuste e devolve o id (None se não houve o que contar).

    O retrato **precisa** ser guardado: depois de gravado, o "antes" não existe
    mais em lugar nenhum, e o gestor não pediu esta mudança — ele tem direito de
    ver o que o sistema fez sozinho (regra 11). O id vira a URL da tela.

    Sem commit: quem chamou está no meio da transação da alteração que disparou
    isto — o reajuste e a sua causa entram juntos ou não entram.
    """
    relevantes = [
        r for r in reajustes
        if r.mudou or r.pulados_por_permuta or r.dias_descobertos
    ]
    if not relevantes:
        return None
    reg = auditoria_service.registrar(
        db, usuario_id=gestor_id, entidade=ENTIDADE, acao=ACAO,
        depois={"origem": origem, "reajustes": [r.resumo() for r in relevantes]},
    )
    db.flush()          # o id é o que monta a URL do redirecionamento
    return reg.id


def escalas_do_militar(db: Session, militar_id: int) -> list[int]:
    """Escalas ativas em que o militar participa — as que o evento dele alcança.

    Inclui participação INATIVA de propósito: isentar alguém é exatamente um dos
    eventos que dispara reajuste, e nesse momento o vínculo já está desativado.
    """
    return list(db.scalars(
        select(Participacao.escala_id)
        .join(EscalaORM, EscalaORM.id == Participacao.escala_id)
        .where(Participacao.militar_id == militar_id, EscalaORM.ativa.is_(True))
        .distinct()
    ))


def reajustar_por_militar(
    db: Session, militar_id: int, a_partir_de: date, hoje: date | None = None,
) -> list[Reajuste]:
    """Reajusta todas as escalas alcançadas por um evento de UMA pessoa.

    Dispensa, isenção ou desativação mudam a fila de toda escala em que ela
    concorre — inclusive as concorrentes, que sentem a mudança pela folga na
    própria escalação de cada dia.
    """
    saida = []
    for escala_id in escalas_do_militar(db, militar_id):
        r = reajustar(db, escala_id, a_partir_de, hoje)
        if r.mudou or r.dias_processados:
            saida.append(r)
    return saida
