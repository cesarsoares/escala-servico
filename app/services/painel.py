"""O que o painel do gestor mostra (regra 11 — visão de gestão).

Fica na camada de serviço, e não na tela, por dois motivos: cada bloco é
testável sem HTTP, e nenhum deles inventa regra — todos leem o que já está
gravado ou perguntam ao motor (`services/rotacao`), que é quem sabe a regra.

O painel responde a quatro perguntas, nesta ordem de urgência:
  1. a escala está fechada até quando? (buraco de cobertura)
  2. o que exige ação hoje? (conflito, efetivo curto, escala mal configurada)
  3. quem serve hoje/amanhã e quem entra depois?
  4. a distribuição está justa e o cadastro sustenta as regras de desempate?
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.domain.calendario import classificar_dia
from app.models.calendario import Feriado
from app.models.escala import Escala, Participacao, Posto
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import CirculoHierarquico, PostoGraduacao
from app.models.servico import Permuta, Servico
from app.services import calendario_service, mapeamento, rotacao

HORIZONTE_DIAS = 30      # janela do painel: o mês à frente é o que se fecha


# --- 1. Cobertura ------------------------------------------------------------
@dataclass
class Cobertura:
    """Até quando a escala está fechada e o que falta na janela à frente."""
    escala: Escala
    fechada_ate: date | None            # último dia COM serviço gravado
    descobertos: list[date]             # dias sem serviço na janela (só os que a escala roda)
    total_na_janela: int

    @property
    def em_dia(self) -> bool:
        return not self.descobertos

    @property
    def primeiro_descoberto(self) -> date | None:
        return self.descobertos[0] if self.descobertos else None

    # --- para a barra de cobertura da tela ---
    @property
    def cobertos(self) -> int:
        return self.total_na_janela - len(self.descobertos)

    @property
    def pct_coberto(self) -> int:
        """Fatia coberta da janela, em %. Sem dia nenhum a janela é 100% (a
        escala só-vermelha numa semana sem fim de semana não está 'descoberta')."""
        if not self.total_na_janela:
            return 100
        return round(self.cobertos * 100 / self.total_na_janela)


def cobertura(db: Session, hoje: date, dias: int = HORIZONTE_DIAS) -> list[Cobertura]:
    """Dias sem ninguém escalado, de hoje até `dias` à frente, por escala.

    Só conta como descoberto o dia que a escala REALMENTE roda: a escala do
    museu é só-vermelha e não deve acusar buraco em dia útil (regra 4.5).
    """
    fim = hoje + timedelta(days=dias)
    feriados = calendario_service.feriados(db, hoje, fim)
    ov_verm = calendario_service.overrides_vermelha(db, hoje, fim)
    ov_preta = calendario_service.overrides_preta(db, hoje, fim)

    saida = []
    for escala in db.scalars(
        select(Escala).where(Escala.ativa.is_(True)).order_by(Escala.nome)
    ):
        com_servico = {
            d for (d,) in db.execute(
                select(Servico.dia).where(
                    Servico.escala_id == escala.id,
                    Servico.dia >= hoje, Servico.dia <= fim).distinct()
            ).all()
        }
        e_dom = mapeamento.escala_para_dominio(db, escala)
        descobertos, total = [], 0
        dia = hoje
        while dia <= fim:
            cor = classificar_dia(dia, feriados, ov_verm, ov_preta)
            if rotacao.escala_roda_cor(e_dom, cor):
                total += 1
                if dia not in com_servico:
                    descobertos.append(dia)
            dia += timedelta(days=1)
        saida.append(Cobertura(
            escala=escala,
            fechada_ate=db.scalar(
                select(func.max(Servico.dia)).where(Servico.escala_id == escala.id)),
            descobertos=descobertos,
            total_na_janela=total,
        ))
    return saida


# --- 2. Exige atenção --------------------------------------------------------
@dataclass
class Alertas:
    conflitos: list[dict] = field(default_factory=list)      # escalado E impedido
    efetivo_curto: list[dict] = field(default_factory=list)  # menos militares que postos
    mal_configuradas: list[dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.conflitos) + len(self.efetivo_curto) + len(self.mal_configuradas)


def alertas(db: Session, hoje: date, dias: int = HORIZONTE_DIAS) -> Alertas:
    """O que precisa de decisão do gestor na janela à frente."""
    fim = hoje + timedelta(days=dias)
    a = Alertas()

    # (a) escalado E impedido no mesmo dia — aparece quando se lança a dispensa
    # DEPOIS de fechar o mês. O motor não erra; o que estava gravado é que
    # envelheceu. A saída é re-escalar o período com 'regravar' (regra 7.5).
    linhas = db.execute(
        select(Servico.dia, Escala.nome, Militar.nome_guerra, PostoGraduacao.sigla,
               Impedimento.inicio, Impedimento.fim, Escala.id)
        .join(Escala, Escala.id == Servico.escala_id)
        .join(Militar, Militar.id == Servico.militar_id)
        .join(PostoGraduacao, PostoGraduacao.id == Militar.posto_graduacao_id)
        .join(Impedimento, Impedimento.militar_id == Servico.militar_id)
        .where(Impedimento.inicio <= Servico.dia, Servico.dia <= Impedimento.fim,
               Servico.dia >= hoje, Servico.dia <= fim)
        .order_by(Servico.dia)
    ).all()
    a.conflitos = [
        {"dia": d, "escala": esc, "escala_id": eid, "militar": f"{sigla} {guerra}",
         "impedimento": (ini, f_)}
        for d, esc, guerra, sigla, ini, f_, eid in linhas
    ]

    # (b) dia fechado com MENOS militares do que postos (regra 7.8). Lê o que
    # está gravado — é o retrato que a impressão vai mostrar.
    postos_por_escala = dict(db.execute(
        select(Posto.escala_id, func.count()).group_by(Posto.escala_id)).all())
    gravados = db.execute(
        select(Servico.escala_id, Servico.dia, func.count())
        .where(Servico.dia >= hoje, Servico.dia <= fim)
        .group_by(Servico.escala_id, Servico.dia)
    ).all()
    nomes = dict(db.execute(select(Escala.id, Escala.nome)).all())
    a.efetivo_curto = [
        {"dia": dia, "escala": nomes.get(eid), "escala_id": eid,
         "gravados": n, "postos": postos_por_escala.get(eid, 0)}
        for eid, dia, n in gravados if n < postos_por_escala.get(eid, 0)
    ]
    a.efetivo_curto.sort(key=lambda x: x["dia"])

    # (c) escala que não tem como rodar: menos participantes que vagas (regra 7.8)
    participantes = dict(db.execute(
        select(Participacao.escala_id, func.count())
        .where(Participacao.ativo.is_(True)).group_by(Participacao.escala_id)).all())
    for escala in db.scalars(select(Escala).where(Escala.ativa.is_(True)).order_by(Escala.nome)):
        vagas = postos_por_escala.get(escala.id, 0)
        gente = participantes.get(escala.id, 0)
        if gente < vagas:
            a.mal_configuradas.append({
                "escala": escala.nome, "escala_id": escala.id,
                "participantes": gente, "postos": vagas,
            })
    return a


# --- 3. Serviço do dia e próximos da fila ------------------------------------
def servico_do_dia(db: Session, dia: date) -> list[dict]:
    """Quem serve no dia, por escala, com a cobertura de permuta (regra 9)."""
    servicos = db.scalars(
        select(Servico).where(Servico.dia == dia)
        .options(joinedload(Servico.militar).joinedload(Militar.posto_graduacao))
        .order_by(Servico.escala_id, Servico.posto_id)
    ).all()
    if not servicos:
        return []

    escalas = dict(db.execute(select(Escala.id, Escala.nome)).all())
    permutas = {p.servico_id: p for p in db.scalars(
        select(Permuta).where(Permuta.servico_id.in_([s.id for s in servicos])))}
    subs = {m.id: m for m in db.scalars(
        select(Militar).options(joinedload(Militar.posto_graduacao))
        .where(Militar.id.in_({p.militar_substituto_id for p in permutas.values()})))
    } if permutas else {}

    saida = []
    for s in servicos:
        p = permutas.get(s.id)
        sub = subs.get(p.militar_substituto_id) if p else None
        saida.append({
            "escala": escalas.get(s.escala_id), "escala_id": s.escala_id,
            "cor": s.cor, "militar": f"{s.militar.posto_graduacao.sigla} {s.militar.nome_guerra}",
            # A folga é de quem estava escalado, nunca de quem cobriu (regra 9),
            # então o escalado continua aparecendo mesmo permutado.
            "substituto": f"{sub.posto_graduacao.sigla} {sub.nome_guerra}" if sub else None,
            "inicio": s.inicio_dt, "termino": s.termino_dt,
        })
    return saida


def proximos_da_fila(db: Session, dia: date, quantos: int = 3) -> list[dict]:
    """Quem o motor escalaria a seguir, por escala — SEM gravar nada.

    Roda a mesma função da escalação real (`rotacao.escalar_dia`), então a
    resposta não é uma segunda implementação da regra: é a regra.
    """
    saida = []
    for escala in db.scalars(
        select(Escala).where(Escala.ativa.is_(True)).order_by(Escala.nome)
    ):
        resultado = rotacao.escalar_dia(db, escala.id, dia)
        if not resultado.escolhidos:
            continue
        saida.append({
            "escala": escala.nome, "escala_id": escala.id, "dia": dia,
            "cor": resultado.cor,
            "militares": [f"{m.posto} {m.nome_guerra}" for m in resultado.escolhidos[:quantos]],
        })
    return saida


# --- 4. Saúde do cadastro e equidade -----------------------------------------
@dataclass
class SaudeCadastro:
    """Campos em falta que a REGRA usa — não é capricho de cadastro.

    Sem data de promoção (9.2), número de antiguidade da praça (9.5) e data de
    nascimento (art. 17), o desempate da fila cai fora dos critérios legais.
    """
    ativos: int
    sem_identidade: int
    sem_promocao: int
    sem_nascimento: int
    pracas: int
    pracas_sem_antiguidade: int
    sem_escala: int

    @property
    def completo(self) -> bool:
        return not (self.sem_promocao or self.pracas_sem_antiguidade or self.sem_nascimento)

    @property
    def campos(self) -> list[CampoCadastro]:
        """Um item por campo, para a barra de completude da tela.

        Ordem deliberada: primeiro o que a regra usa para desempatar a fila
        (9.5, 9.2, art. 17), depois o resto.
        """
        return [
            CampoCadastro("Número de antiguidade", self.pracas - self.pracas_sem_antiguidade,
                          self.pracas, "praças", f"faltam {self.pracas_sem_antiguidade}"),
            CampoCadastro("Data de promoção", self.ativos - self.sem_promocao,
                          self.ativos, "militares", f"faltam {self.sem_promocao}"),
            CampoCadastro("Data de nascimento", self.ativos - self.sem_nascimento,
                          self.ativos, "militares", f"faltam {self.sem_nascimento}"),
            CampoCadastro("Identidade militar", self.ativos - self.sem_identidade,
                          self.ativos, "militares", f"faltam {self.sem_identidade}"),
            CampoCadastro("Participa de alguma escala", self.ativos - self.sem_escala,
                          self.ativos, "ativos", f"{self.sem_escala} sem escala"),
        ]


@dataclass
class CampoCadastro:
    """Uma barra 'preenchido × faltando' da completude do cadastro."""
    rotulo: str
    ok: int
    total: int
    unidade: str
    aviso: str

    @property
    def faltam(self) -> int:
        return self.total - self.ok

    @property
    def pct_ok(self) -> int:
        return 100 if not self.total else round(self.ok * 100 / self.total)

    @property
    def completo(self) -> bool:
        return self.faltam == 0


def saude_cadastro(db: Session) -> SaudeCadastro:
    def contar(*condicoes):
        return db.scalar(select(func.count()).select_from(Militar)
                         .where(Militar.ativo.is_(True), *condicoes))

    pracas = db.scalar(
        select(func.count()).select_from(Militar)
        .join(PostoGraduacao, PostoGraduacao.id == Militar.posto_graduacao_id)
        .join(CirculoHierarquico, CirculoHierarquico.id == PostoGraduacao.circulo_id)
        .where(Militar.ativo.is_(True), CirculoHierarquico.eh_praca.is_(True)))
    pracas_sem = db.scalar(
        select(func.count()).select_from(Militar)
        .join(PostoGraduacao, PostoGraduacao.id == Militar.posto_graduacao_id)
        .join(CirculoHierarquico, CirculoHierarquico.id == PostoGraduacao.circulo_id)
        .where(Militar.ativo.is_(True), CirculoHierarquico.eh_praca.is_(True),
               Militar.numero_antiguidade.is_(None)))
    return SaudeCadastro(
        ativos=contar(),
        sem_identidade=contar(Militar.identidade.is_(None)),
        sem_promocao=contar(Militar.data_promocao.is_(None)),
        sem_nascimento=contar(Militar.data_nascimento.is_(None)),
        pracas=pracas,
        pracas_sem_antiguidade=pracas_sem,
        sem_escala=contar(~Militar.id.in_(
            select(Participacao.militar_id).where(Participacao.ativo.is_(True)))),
    )


@dataclass
class Equidade:
    """Distribuição de serviços no período, por escala (a cobrança clássica)."""
    escala: str
    escala_id: int
    participantes: int
    servidos: int          # quantos pegaram ao menos um serviço
    minimo: int
    maximo: int

    @property
    def nunca_serviram(self) -> int:
        return self.participantes - self.servidos

    @property
    def desequilibrio(self) -> int:
        """Diferença entre quem mais e quem menos serviu — inclui quem não serviu."""
        return self.maximo - (0 if self.nunca_serviram else self.minimo)

    @property
    def vigiar(self) -> bool:
        """Diferença de 2 serviços ou mais já merece explicação."""
        return self.desequilibrio >= 2

    # --- posições da barra de amplitude (escala comum 0..máximo) ---
    @property
    def piso(self) -> int:
        """Quem serviu menos — 0 quando alguém ainda não serviu nenhuma vez."""
        return 0 if self.nunca_serviram else self.minimo

    @property
    def pct_piso(self) -> int:
        return 0 if not self.maximo else round(self.piso * 100 / self.maximo)

    @property
    def pct_amplitude(self) -> int:
        return 0 if not self.maximo else round((self.maximo - self.piso) * 100 / self.maximo)

    @property
    def pct_serviram(self) -> int:
        return 0 if not self.participantes else round(self.servidos * 100 / self.participantes)


def equidade(db: Session, inicio: date, fim: date) -> list[Equidade]:
    saida = []
    for escala in db.scalars(
        select(Escala).where(Escala.ativa.is_(True)).order_by(Escala.nome)
    ):
        contagens = [n for _, n in db.execute(
            select(Servico.militar_id, func.count())
            .where(Servico.escala_id == escala.id,
                   Servico.dia >= inicio, Servico.dia <= fim)
            .group_by(Servico.militar_id)).all()]
        participantes = db.scalar(
            select(func.count()).select_from(Participacao)
            .where(Participacao.escala_id == escala.id, Participacao.ativo.is_(True)))
        saida.append(Equidade(
            escala=escala.nome, escala_id=escala.id, participantes=participantes,
            servidos=len(contagens),
            minimo=min(contagens) if contagens else 0,
            maximo=max(contagens) if contagens else 0,
        ))
    return saida


@dataclass
class LugarNaFila:
    """Um participante no ranking de serviços da escala."""
    militar: Militar
    servicos: int
    pct: int              # largura da barra, relativa a quem mais serviu
    proximo: bool         # está no menor número de serviços do grupo


def fila_por_servicos(db: Session, escala_id: int, inicio: date, fim: date) -> list[LugarNaFila]:
    """Participantes ativos ordenados por quantidade de serviços no período.

    É uma leitura de EQUIDADE, não a fila do motor: quem serviu menos aparece
    primeiro, e `proximo` marca os empatados no menor número. A ordem real de
    quem entra amanhã sai do motor (regra 6), que também pesa folga, cor,
    impedimento e antiguidade — por isso a tela chama isto de leitura, não de
    previsão.
    """
    participantes = db.scalars(
        select(Militar)
        .join(Participacao, Participacao.militar_id == Militar.id)
        .where(Participacao.escala_id == escala_id, Participacao.ativo.is_(True),
               Militar.ativo.is_(True))
        .options(joinedload(Militar.posto_graduacao))
        .order_by(Militar.nome_guerra)
    ).all()
    if not participantes:
        return []

    contagem = dict(db.execute(
        select(Servico.militar_id, func.count())
        .where(Servico.escala_id == escala_id,
               Servico.dia >= inicio, Servico.dia <= fim)
        .group_by(Servico.militar_id)
    ).all())

    quantos = {m.id: contagem.get(m.id, 0) for m in participantes}
    maximo = max(quantos.values())
    minimo = min(quantos.values())
    ordenados = sorted(participantes, key=lambda m: (quantos[m.id], m.nome_guerra))
    return [
        LugarNaFila(
            militar=m, servicos=quantos[m.id],
            # Barra de largura mínima quando o militar nunca serviu: zero
            # invisível não comunica "este é o próximo".
            pct=6 if not maximo or not quantos[m.id] else max(6, round(quantos[m.id] * 100 / maximo)),
            proximo=quantos[m.id] == minimo,
        )
        for m in ordenados
    ]


MAX_BARRAS_TEMPO = 15      # linha do tempo com mais que isso vira mancha


@dataclass
class BarraTempo:
    """Um impedimento posicionado na linha do tempo."""
    impedimento: Impedimento
    militar: Militar | None
    left: float
    width: float
    situacao: str          # 'emcurso' | 'futuro' | 'passado'


@dataclass
class LinhaDoTempo:
    inicio: date
    fim: date
    barras: list[BarraTempo]
    marcas: list[tuple[str, float]]     # (rótulo, posição %)
    pct_hoje: float | None              # None se hoje cai fora da janela
    ocultos: int                        # quantos não couberam


_MES_CURTO = ("", "jan", "fev", "mar", "abr", "mai", "jun",
              "jul", "ago", "set", "out", "nov", "dez")


def linha_do_tempo(impedimentos, militares: dict, hoje: date) -> LinhaDoTempo | None:
    """Posiciona os impedimentos numa faixa de tempo comum.

    Mostra só o que ainda importa — em curso e futuros —, porque impedimento
    encerrado não muda escalação nenhuma e uma faixa com o histórico inteiro
    vira mancha. A janela é a menor que contém `hoje` e todos os exibidos.
    """
    relevantes = sorted((i for i in impedimentos if i.fim >= hoje),
                        key=lambda i: (i.inicio, i.fim))
    if not relevantes:
        return None
    ocultos = max(0, len(relevantes) - MAX_BARRAS_TEMPO)
    relevantes = relevantes[:MAX_BARRAS_TEMPO]

    inicio = min([i.inicio for i in relevantes] + [hoje])
    fim = max([i.fim for i in relevantes] + [hoje])
    dias = (fim - inicio).days or 1

    def pct(d: date) -> float:
        return round((d - inicio).days * 100 / dias, 1)

    barras = []
    for imp in relevantes:
        largura = round(((imp.fim - imp.inicio).days + 1) * 100 / dias, 1)
        barras.append(BarraTempo(
            impedimento=imp, militar=militares.get(imp.militar_id),
            left=pct(imp.inicio), width=min(largura, 100 - pct(imp.inicio)),
            situacao=("emcurso" if imp.inicio <= hoje <= imp.fim
                      else "futuro" if imp.inicio > hoje else "passado"),
        ))

    marcas = []
    for fracao in (0, 1 / 3, 2 / 3, 1):
        d = inicio + timedelta(days=round(dias * fracao))
        marcas.append((f"{d.day} {_MES_CURTO[d.month]}", round(fracao * 100, 1)))
    return LinhaDoTempo(inicio=inicio, fim=fim, barras=barras, marcas=marcas,
                        pct_hoje=pct(hoje), ocultos=ocultos)


def dias_vermelhos_proximos(db: Session, hoje: date, dias: int = HORIZONTE_DIAS) -> list[dict]:
    """Feriados da janela — é o que transforma dia útil em vermelha (regra 5)."""
    fim = hoje + timedelta(days=dias)
    return [{"data": f.data, "nome": f.nome}
            for f in db.scalars(
                select(Feriado).where(Feriado.data >= hoje, Feriado.data <= fim)
                .order_by(Feriado.data))]
