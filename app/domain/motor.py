"""Motor de rotação — quem é o próximo a servir (seção 6 das regras)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.domain.antiguidade import comparar_antiguidade
from app.domain.folga import folga_efetiva_horas, respeita_folga_minima
from app.domain.models import Cor, Escala, Impedimento, Militar, Participacao

# Por que um participante não entrou no dia. O motor sempre soube — só não
# contava: a tela dizia "efetivo insuficiente" e o gestor ficava sem saber se
# faltava gente, se todo mundo estava de férias ou se a folga não tinha fechado.
MOTIVO_INATIVO = "inativo"      # regra 7.6 — isento desta escala
MOTIVO_COR = "cor"              # regra 3.3.1 — não concorre nesta cor
MOTIVO_IMPEDIDO = "impedido"    # regra 7.5 — dispensa/férias/curso no dia
MOTIVO_FOLGA = "folga"          # regra 7.4 — ainda não completou a folga mínima


@dataclass(frozen=True)
class Preterido:
    """Um participante que NÃO entrou no dia, com o motivo."""
    militar: Militar
    motivo: str
    # Só no motivo 'folga': quando ele passa a poder assumir. É o que transforma
    # "sem folga" em informação acionável — o gestor vê a partir de quando conta.
    livre_em: datetime | None = None


@dataclass
class Selecao:
    """Resultado de um dia: quem entrou e, dos que não entraram, por quê."""
    escolhidos: list[Participacao] = field(default_factory=list)
    preteridos: list[Preterido] = field(default_factory=list)


def _data_minima() -> date:
    return date.min


def fila_ordenada(participacoes: list[Participacao], cor: Cor) -> list[Participacao]:
    """Ordena a fila: mais folgado primeiro; empate resolvido por antiguidade.

    Regra 6.2: 'mais folgado' = serviu há mais tempo naquela cor (data mais antiga).
    Quem nunca serviu (None) vai para o topo. Regra 6.3/seção 9: desempate.

    Quem não concorre nesta cor (regra 3.3.1) fica FORA da fila — não é o mesmo
    que ser pulado por impedimento (6.4): ali o militar guarda a vez, aqui ele
    nunca teve vez nesta cor.
    """
    ativos = [p for p in participacoes if p.ativo and p.serve_cor(cor)]  # regras 7.6 e 3.3.1

    def chave(p: Participacao):
        return p.ultimo_na_cor(cor) or _data_minima()

    def cmp(pa: Participacao, pb: Participacao) -> int:
        ka, kb = chave(pa), chave(pb)
        if ka != kb:
            return -1 if ka < kb else 1  # data mais antiga primeiro
        return comparar_antiguidade(pa.militar, pb.militar)

    from functools import cmp_to_key
    return sorted(ativos, key=cmp_to_key(cmp))


def disponivel(
    p: Participacao,
    dia: date,
    impedimentos: list[Impedimento],
    ultimo_termino_global: datetime | None,
    inicio_candidato: datetime,
    folga_minima_horas: int | None = None,
) -> bool:
    """Regra 7: sem impedimento no dia E respeitando a folga mínima da escala.

    `inicio_candidato` é o início do serviço a assumir (janela da escala de
    destino); `folga_minima_horas` é o piso dessa escala (regra 7.4.2);
    `ultimo_termino_global` é o término do último serviço do militar em qualquer
    cor e qualquer escala concorrente (regra 7.4).
    """
    for imp in impedimentos:
        if imp.militar_id == p.militar.id and imp.cobre(dia):
            return False  # regra 7.5
    return respeita_folga_minima(ultimo_termino_global, inicio_candidato, folga_minima_horas)


def _impedido_no_dia(p: Participacao, dia: date, impedimentos: list[Impedimento]) -> bool:
    """Regra 7.5, isolada de `disponivel` para o motivo poder ser nomeado."""
    return any(
        imp.militar_id == p.militar.id and imp.cobre(dia) for imp in impedimentos
    )


def selecionar(
    escala: Escala,
    participacoes: list[Participacao],
    cor: Cor,
    dia: date,
    impedimentos: list[Impedimento],
    ultimo_termino_por_militar: dict[int, datetime],
) -> Selecao:
    """Quem serve no dia E por que os demais ficaram de fora.

    Mesma regra de `proximos` (que hoje é açúcar sobre esta): percorre a fila do
    mais folgado ao menos folgado e pega os `escala.postos` primeiros
    disponíveis (6.1); impedido é pulado sem perder a vez (6.4).

    O que muda é só o que se guarda pelo caminho. Quando o dia fecha com menos
    militares que postos (7.8), a diferença entre "não tem gente na escala",
    "estão todos de férias" e "a folga não fechou" é a única informação que
    permite agir — e ela morria dentro do laço.

    ⚠️ Os `preteridos` são completos apenas quando o dia NÃO fecha: com os postos
    preenchidos o laço para, e quem estava adiante na fila nunca foi examinado.
    É o comportamento certo — quem está abaixo da linha de corte não foi
    "recusado", só não chegou a vez dele.
    """
    inicio_candidato = escala.inicio_em(dia)
    sel = Selecao()

    # Fora da fila desde sempre (não é "pulado": nunca teve vez neste dia).
    # Entram no relatório porque são metade da resposta a "por que faltou gente".
    for p in participacoes:
        if not p.ativo:
            sel.preteridos.append(Preterido(p.militar, MOTIVO_INATIVO))
        elif not p.serve_cor(cor):
            sel.preteridos.append(Preterido(p.militar, MOTIVO_COR))

    folga = timedelta(hours=folga_efetiva_horas(escala.folga_minima_horas))
    for p in fila_ordenada(participacoes, cor):
        if len(sel.escolhidos) >= escala.postos:
            break
        ultimo = ultimo_termino_por_militar.get(p.militar.id)
        if _impedido_no_dia(p, dia, impedimentos):
            sel.preteridos.append(Preterido(p.militar, MOTIVO_IMPEDIDO))
        elif not respeita_folga_minima(ultimo, inicio_candidato, escala.folga_minima_horas):
            # `ultimo` não é None aqui: quem nunca serviu sempre respeita a folga.
            sel.preteridos.append(Preterido(p.militar, MOTIVO_FOLGA, ultimo + folga))
        else:
            sel.escolhidos.append(p)
    return sel


def proximos(
    escala: Escala,
    participacoes: list[Participacao],
    cor: Cor,
    dia: date,
    impedimentos: list[Impedimento],
    ultimo_termino_por_militar: dict[int, datetime],
) -> list[Participacao]:
    """Escolhe os mais folgados disponíveis para preencher os postos do dia.

    Regra 6.1: percorre a fila do mais folgado ao menos folgado e pega os
    `escala.postos` primeiros disponíveis. A janela e a folga usadas são as da
    própria escala (regras 2.4 e 7.4.2). Quem está impedido é apenas pulado; a
    fila não muda de ordem (regra 6.4).

    Se retornar MENOS que `escala.postos`, o efetivo é insuficiente para o piso
    naquele dia (regra 7.8): cabe ao caller avisar o gestor — e `selecionar`
    devolve o MOTIVO de cada ausência, que é o que a tela precisa mostrar.

    `ultimo_termino_por_militar` traz o término do serviço mais recente de cada
    militar nas escalas concorrentes (montado pela camada de serviço).
    """
    return selecionar(
        escala, participacoes, cor, dia, impedimentos, ultimo_termino_por_militar,
    ).escolhidos


def proximo(
    escala: Escala,
    participacoes: list[Participacao],
    cor: Cor,
    dia: date,
    impedimentos: list[Impedimento],
    ultimo_termino_por_militar: dict[int, datetime],
) -> Participacao | None:
    """Caso de 1 posto: o primeiro da fila que estiver disponível (regra 6.2).

    Açúcar para `proximos(...)` pegando o primeiro escolhido.
    """
    escolhidos = proximos(
        escala, participacoes, cor, dia, impedimentos, ultimo_termino_por_militar,
    )
    return escolhidos[0] if escolhidos else None
