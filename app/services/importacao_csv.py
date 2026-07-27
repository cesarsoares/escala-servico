"""Importação do HISTÓRICO DE SERVIÇOS em CSV (carga de uma OM nova).

Para que serve: a OM cadastra o efetivo e cria as escalas, mas o passado —
quem serviu em que dia — está numa planilha. Sem esse passado o motor não tem
fila: todo mundo empata em "nunca serviu" e a primeira escalação sai pela
antiguidade, ignorando quem acabou de sair de serviço.

Como funciona, e por que assim:

  - **Duas etapas.** Ler o arquivo NÃO grava nada; devolve o relatório linha a
    linha, e só o "confirmar" persiste. É o mesmo princípio da importação da
    ficha em PDF: o operador confere antes.
  - **Nada é chutado.** Militar que não casa, escala desconhecida, data
    ilegível, posto inexistente — a linha é RECUSADA com o motivo, nunca
    resolvida no palpite. Nome de guerra repetido em duas OMs sem a coluna `om`
    é ambiguidade, e ambiguidade também é recusa.
  - **Serviço importado é FATO CONSUMADO.** Não passa pelo motor: o que está no
    papel aconteceu, ainda que hoje ferisse a folga mínima (regra 7.2) ou que o
    militar não seja mais participante da escala. Esses casos viram AVISO, não
    recusa — recusá-los impediria de carregar exatamente o histórico que se quer
    registrar. Contam normalmente para a fila e para a folga daí em diante.
  - **Excel em português** escreve CSV com `;` e acentuação em cp1252. O leitor
    aceita `;` ou `,` e tenta utf-8 (com ou sem BOM) antes de cp1252 — senão a
    primeira importação real morre num "Ã§".
"""
from __future__ import annotations

import csv
import io
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.calendario import classificar_dia
from app.domain.models import Cor
from app.models.escala import Escala, Participacao, Posto
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar
from app.models.servico import Servico
from app.services import calendario_service

# Cabeçalho aceito. `posto` e `om` são opcionais: a escala de uma vaga só não
# tem posto para informar, e a OM só é necessária para desfazer homonímia.
COLUNAS = ("escala", "data", "militar", "posto", "om")
OBRIGATORIAS = ("escala", "data", "militar")

MAX_LINHAS = 20_000        # ~27 anos de uma escala diária: acima disso é engano
MAX_BYTES = 4 * 1024 * 1024

_FORMATOS_DATA = ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%Y")


def _chave(texto: str) -> str:
    """Comparação tolerante: sem acento, sem caixa, sem espaço duplicado.

    'Oficial de Dia  do QG' e 'OFICIAL DE DIA DO QG' são a mesma escala para
    quem digitou a planilha, e recusar por isso seria pedantismo.
    """
    sem_acento = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return " ".join(sem_acento.lower().split())


def _data(bruto: str) -> date | None:
    texto = (bruto or "").strip()
    for formato in _FORMATOS_DATA:
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def decodificar(conteudo: bytes) -> str:
    """utf-8 (com ou sem BOM) e, por último, cp1252 — o que o Excel pt-BR gera."""
    for codec in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return conteudo.decode(codec)
        except UnicodeDecodeError:
            continue
    return conteudo.decode("latin-1", errors="replace")


@dataclass
class Linha:
    """Uma linha do arquivo, já confrontada com o banco."""
    numero: int                       # linha do arquivo, para o operador achar
    bruto: dict[str, str]
    escala_id: int | None = None
    posto_id: int | None = None
    militar_id: int | None = None
    dia: date | None = None
    erro: str | None = None           # recusada
    avisos: list[str] = field(default_factory=list)   # grava, mas com ressalva

    @property
    def aceita(self) -> bool:
        return self.erro is None


@dataclass
class Leitura:
    linhas: list[Linha] = field(default_factory=list)
    erro_geral: str | None = None     # arquivo inteiro imprestável

    @property
    def aceitas(self) -> list[Linha]:
        return [linha for linha in self.linhas if linha.aceita]

    @property
    def recusadas(self) -> list[Linha]:
        return [linha for linha in self.linhas if not linha.aceita]

    @property
    def com_aviso(self) -> list[Linha]:
        return [linha for linha in self.linhas if linha.aceita and linha.avisos]


def _dialeto(texto: str) -> str:
    """`;` do Excel pt-BR ou `,` do padrão. Decide pela primeira linha."""
    cabecalho = texto.splitlines()[0] if texto.strip() else ""
    return ";" if cabecalho.count(";") >= cabecalho.count(",") else ","


def ler(db: Session, conteudo: bytes) -> Leitura:
    """Lê o CSV e confronta cada linha com o banco. NÃO grava nada."""
    if len(conteudo) > MAX_BYTES:
        return Leitura(erro_geral="Arquivo grande demais (limite de 4 MB).")
    texto = decodificar(conteudo)
    if not texto.strip():
        return Leitura(erro_geral="Arquivo vazio.")

    leitor = csv.DictReader(io.StringIO(texto), delimiter=_dialeto(texto))
    campos = [_chave(c) for c in (leitor.fieldnames or [])]
    faltando = [c for c in OBRIGATORIAS if c not in campos]
    if faltando:
        return Leitura(erro_geral=(
            "Faltam colunas no cabeçalho: " + ", ".join(faltando)
            + ". Esperado: " + ";".join(COLUNAS) + ". Baixe o modelo."))

    # Índices de reconciliação, montados uma vez só.
    escalas = {_chave(e.nome): e for e in db.scalars(select(Escala))}
    postos: dict[int, list[Posto]] = {}
    for p in db.scalars(select(Posto).order_by(Posto.ordem)):
        postos.setdefault(p.escala_id, []).append(p)
    oms = {_chave(o.sigla): o.id for o in db.scalars(select(OrganizacaoMilitar))}

    por_nome: dict[str, list[Militar]] = {}
    for m in db.scalars(select(Militar)):
        por_nome.setdefault(_chave(m.nome_guerra), []).append(m)

    # (escala, militar) -> em que cores concorre hoje (regra 3.3.1)
    participantes = {
        (p.escala_id, p.militar_id): (p.serve_preta, p.serve_vermelha)
        for p in db.scalars(select(Participacao).where(Participacao.ativo.is_(True)))
    }
    ja_no_banco = {
        (posto_id, dia) for posto_id, dia in
        db.execute(select(Servico.posto_id, Servico.dia)).all()
    }

    leitura = Leitura()
    vistos: dict[tuple[int, date], int] = {}      # (posto, dia) -> linha do arquivo

    for numero, cru in enumerate(leitor, start=2):    # 1 é o cabeçalho
        if len(leitura.linhas) >= MAX_LINHAS:
            leitura.erro_geral = f"Arquivo com mais de {MAX_LINHAS} linhas."
            break
        bruto = {_chave(k): (v or "").strip() for k, v in cru.items() if k}
        if not any(bruto.get(c) for c in OBRIGATORIAS):
            continue                                   # linha em branco
        linha = Linha(numero=numero, bruto=bruto)
        leitura.linhas.append(linha)

        escala = escalas.get(_chave(bruto.get("escala", "")))
        if escala is None:
            linha.erro = f"Escala '{bruto.get('escala', '')}' não existe."
            continue
        linha.escala_id = escala.id
        if not escala.ativa:
            linha.avisos.append("escala extinta")

        linha.dia = _data(bruto.get("data", ""))
        if linha.dia is None:
            linha.erro = f"Data '{bruto.get('data', '')}' ilegível (use dd/mm/aaaa)."
            continue

        # militar: nome de guerra + OM quando houver homônimo
        candidatos = por_nome.get(_chave(bruto.get("militar", "")), [])
        sigla_om = bruto.get("om", "")
        if sigla_om:
            om_id = oms.get(_chave(sigla_om))
            if om_id is None:
                linha.erro = f"OM '{sigla_om}' não existe."
                continue
            candidatos = [m for m in candidatos if m.om_id == om_id]
        if not candidatos:
            linha.erro = (f"Militar '{bruto.get('militar', '')}'"
                          + (f" da OM '{sigla_om}'" if sigla_om else "")
                          + " não encontrado no efetivo.")
            continue
        if len(candidatos) > 1:
            siglas = ", ".join(sorted({m.om.sigla for m in candidatos}))
            linha.erro = (f"'{bruto.get('militar', '')}' existe em mais de uma OM "
                          f"({siglas}). Informe a coluna 'om'.")
            continue
        militar = candidatos[0]
        linha.militar_id = militar.id
        if not militar.ativo:
            linha.avisos.append("militar desativado")
        if (escala.id, militar.id) not in participantes:
            # Fato consumado: serviu, ainda que hoje não participe (saiu da
            # escala, foi isento). Recusar impediria de carregar o histórico.
            linha.avisos.append("não é participante ativo desta escala")

        # posto: pelo rótulo ou pela ordem; a escala de uma vaga dispensa
        vagas = postos.get(escala.id, [])
        if not vagas:
            linha.erro = f"A escala '{escala.nome}' não tem posto cadastrado."
            continue
        rotulo = bruto.get("posto", "")
        if not rotulo:
            if len(vagas) > 1:
                linha.erro = (f"A escala '{escala.nome}' tem {len(vagas)} postos — "
                              "informe a coluna 'posto'.")
                continue
            posto = vagas[0]
        else:
            achado = next((p for p in vagas if _chave(p.rotulo or "") == _chave(rotulo)), None)
            if achado is None and rotulo.strip().isdigit():
                achado = next((p for p in vagas if p.ordem == int(rotulo)), None)
            if achado is None:
                linha.erro = f"Posto '{rotulo}' não existe na escala '{escala.nome}'."
                continue
            posto = achado
        linha.posto_id = posto.id

        chave = (posto.id, linha.dia)
        if chave in ja_no_banco:
            linha.erro = "Já existe serviço gravado neste posto e dia."
            continue
        if chave in vistos:
            linha.erro = f"Repetida — a linha {vistos[chave]} já usa este posto e dia."
            continue
        vistos[chave] = numero

    _avisar_cor_restrita(db, leitura, participantes)
    return leitura


def _avisar_cor_restrita(db: Session, leitura: Leitura, participantes: dict) -> None:
    """Serviço em cor de que o militar hoje não participa (regra 3.3.1).

    É AVISO, não recusa, pela mesma razão de quem já saiu da escala: o serviço
    aconteceu. Mas costuma denunciar arquivo trocado ou participação mal
    configurada, e calar seria esconder isso. A cor sai do calendário da OM,
    nunca do arquivo (regra 5) — é a mesma classificação que `aplicar` usará.
    """
    aceitas = [linha for linha in leitura.aceitas if linha.dia]
    if not aceitas:
        return
    dias = [linha.dia for linha in aceitas]
    feriados = calendario_service.feriados(db, min(dias), max(dias))
    ov_verm = calendario_service.overrides_vermelha(db, min(dias), max(dias))
    ov_preta = calendario_service.overrides_preta(db, min(dias), max(dias))
    for linha in aceitas:
        cores = participantes.get((linha.escala_id, linha.militar_id))
        if cores is None:
            continue                     # já avisado: não é participante ativo
        serve_preta, serve_vermelha = cores
        cor = classificar_dia(linha.dia, feriados, ov_verm, ov_preta)
        if not (serve_preta if cor is Cor.PRETA else serve_vermelha):
            linha.avisos.append(f"hoje não concorre na {cor.value} nesta escala")


def aplicar(db: Session, leitura: Leitura) -> int:
    """Grava as linhas aceitas. Devolve quantos serviços criou.

    A cor sai do calendário da OM (feriados e overrides, regra 5), não do que
    veio no arquivo: a cor é consequência da data, e aceitar a cor de fora
    permitiria um histórico que contradiz o próprio calendário.
    """
    aceitas = leitura.aceitas
    if not aceitas:
        return 0

    dias = [linha.dia for linha in aceitas if linha.dia]
    primeiro, ultimo = min(dias), max(dias)
    feriados = calendario_service.feriados(db, primeiro, ultimo)
    ov_verm = calendario_service.overrides_vermelha(db, primeiro, ultimo)
    ov_preta = calendario_service.overrides_preta(db, primeiro, ultimo)
    escalas = {e.id: e for e in db.scalars(select(Escala))}

    criados = 0
    for linha in aceitas:
        escala = escalas[linha.escala_id]
        inicio = datetime.combine(linha.dia, escala.inicio_servico)
        db.add(Servico(
            escala_id=linha.escala_id,
            posto_id=linha.posto_id,
            militar_id=linha.militar_id,
            dia=linha.dia,
            cor=classificar_dia(linha.dia, feriados, ov_verm, ov_preta),
            inicio_dt=inicio,
            termino_dt=inicio + timedelta(hours=escala.duracao_horas),
        ))
        criados += 1
    db.flush()
    return criados


def modelo(db: Session) -> str:
    """CSV de exemplo, já com os nomes REAIS das escalas e do efetivo.

    Sem isto ninguém acerta o cabeçalho de primeira, e a coluna 'posto' só faz
    sentido depois de ver como os postos daquela escala se chamam.
    """
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";", lineterminator="\r\n")
    escritor.writerow(COLUNAS)

    escala = db.scalar(select(Escala).where(Escala.ativa.is_(True)).order_by(Escala.nome))
    militar = db.scalar(
        select(Militar).where(Militar.ativo.is_(True)).order_by(Militar.nome_guerra))
    if escala is not None and militar is not None:
        posto = db.scalar(
            select(Posto).where(Posto.escala_id == escala.id).order_by(Posto.ordem))
        n_postos = db.scalar(select(func.count()).select_from(Posto)
                             .where(Posto.escala_id == escala.id)) or 0
        rotulo = "" if n_postos <= 1 else (posto.rotulo or str(posto.ordem) if posto else "")
        escritor.writerow([escala.nome, "05/01/2026", militar.nome_guerra, rotulo,
                           militar.om.sigla])
    else:
        escritor.writerow(["Oficial de Dia", "05/01/2026", "SILVA", "", "Cmdo CMS"])
    return saida.getvalue()
