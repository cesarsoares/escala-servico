"""Leitura da ficha individual do militar em PDF (regra 3.2 — cadastro).

Extrai um RASCUNHO dos dados do militar para pré-preencher o formulário de
cadastro. NÃO grava nada: o operador confere e só então salva. Módulo puro
(só pdfplumber), sem banco nem framework — a reconciliação com as tabelas de
referência fica em `app.services.importacao`.

Dois formatos, ambos suportados:

* **SiCaPEx** ("FICHA CADASTRO - SiCaPEx", sistema antigo). O texto sai em
  ordem de leitura, com os rótulos truncados ("Dt Última", "Nome"), então o
  parser é por expressão regular sobre a linha.
* **SCGPE** ("FICHA INDIVIDUAL", Sistema Corporativo de Gestão de Pessoal). É
  uma grade de 4-5 colunas: uma linha de rótulos e a(s) linha(s) de valores
  abaixo. Ler por linha de texto embaralha as colunas quando um campo vem vazio
  (visto na ficha da POTHIRA: `Data Turma` vazia desloca a data de praça), por
  isso aqui o casamento rótulo→valor é feito por COORDENADA (x0 da coluna).
  Esse formato ainda traz uma marca d'água diagonal cujos caracteres se
  intercalam no texto ("C2ÉSAR"); ela é removida pelo filtro de rotação.

O número de antiguidade (regra 9.5, só praças) NÃO aparece em nenhum dos dois
formatos — segue digitado à mão.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path

import pdfplumber

from app.normalizacao import so_digitos

DATA = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")

# Rótulos das linhas da grade do SCGPE que carregam campos nossos. A linha é
# localizada pelo primeiro rótulo; as colunas saem do x0 de cada rótulo.
_GRADE_SCGPE: tuple[tuple[str, ...], ...] = (
    ("Nome Completo:", "CPF", "Identidade Civil:", "Nº CNH:"),
    ("Categoria CNH:", "Sexo:", "Estado Civil:", "Data Nascimento:"),
    ("Posto Graduação:", "QAS/QMS:", "Nome Guerra:", "Situação Atual:", "Identidade Militar:"),
    ("PREC/CP:", "CP:", "Registro de Alistamento", "Data Turma:", "Última Data Praça:"),
    ("Comando:", "Região Militar:", "OM Atual", "Local da OM:"),
)


@dataclass
class FichaExtraida:
    """Rascunho lido da ficha. Campos ausentes vêm como None."""

    formato: str
    nome_completo: str | None = None
    nome_guerra: str | None = None
    cpf: str | None = None
    identidade: str | None = None
    posto_texto: str | None = None
    om_texto: str | None = None
    data_nascimento: date | None = None
    data_praca: date | None = None
    data_promocao: date | None = None
    avisos: list[str] = field(default_factory=list)

    def como_dict(self) -> dict:
        return asdict(self)


class FichaInvalida(Exception):
    """PDF ilegível ou de formato não reconhecido."""


# --- utilidades de baixo nível -------------------------------------------------
def _sem_rotacao(obj) -> bool:
    """Descarta caracteres rotacionados — é a marca d'água diagonal do SCGPE."""
    if obj.get("object_type") != "char":
        return True
    m = obj.get("matrix")
    return not m or (m[1] == 0 and m[2] == 0)


def _data(texto: str | None) -> date | None:
    if not texto:
        return None
    m = DATA.search(texto)
    if not m:
        return None
    d, mes, a = m.group(1).split("/")
    try:
        return date(int(a), int(mes), int(d))
    except ValueError:
        return None


def _limpo(texto: str | None) -> str | None:
    """Normaliza espaços; trata 'N/A' e vazio como ausente."""
    if texto is None:
        return None
    s = re.sub(r"\s+", " ", texto).strip()
    return None if not s or s.upper() in {"N/A", "-", "--"} else s


def _digitos(texto: str | None) -> str | None:
    """Só os dígitos (CPF/identidade) — mantém zero à esquerda, evita duplicata
    entre '605.126.360-87' e '60512636087'.

    A regra é compartilhada com a API e com o formulário da gestão, por isso
    mora em `app.normalizacao`."""
    return so_digitos(texto)


def _linhas_por_pagina(pdf) -> list[list[str]]:
    return [(p.filter(_sem_rotacao).extract_text() or "").splitlines() for p in pdf.pages]


def _secao(linhas: list[str], titulo: re.Pattern, limite: int = 30) -> list[str]:
    """Linhas logo abaixo de um título de seção (quem lê decide onde parar)."""
    for i, linha in enumerate(linhas):
        if titulo.search(linha.strip()):
            return linhas[i + 1: i + 1 + limite]
    return []


def _linhas_da_tabela(linhas: list[str], casa) -> list[str]:
    """Linhas de dados de uma tabela: pula o cabeçalho e para na seção seguinte.

    Sem essa parada a leitura vaza para as seções de baixo (o SiCaPEx põe
    AFASTAMENTOS logo abaixo das datas de praça, cheio de datas).
    """
    dados: list[str] = []
    for linha in linhas:
        s = linha.strip()
        if casa(s):
            dados.append(s)
        elif dados:
            break     # já estava na tabela e a linha não pertence mais a ela
    return dados


# --- promoções e data de praça (tabelas parecidas nos dois formatos) -----------
_LINHA_PROMOCAO = re.compile(r"^(\S+)\s+(.+?)\s+(\d{2}/\d{2}/\d{4})")
_COMECA_COM_DATA = re.compile(r"^\d{2}/\d{2}/\d{4}\b")


def _promocao(linhas_secao: list[str], posto: str | None) -> tuple[date | None, str | None]:
    """Data da promoção ao posto ATUAL, lida da tabela de promoções.

    A tabela vem da promoção mais recente para a mais antiga, uma linha por
    posto: `<tipo> <posto/graduação> <data> <documento>`. Casa pelo posto atual;
    não achando, usa a primeira linha e avisa.
    """
    # Uma linha longa quebra e a continuação começa com data — ela não fecha a
    # tabela (visto na ficha do ARTHUR, cujo documento de promoção quebra).
    linhas = _linhas_da_tabela(
        linhas_secao,
        lambda s: bool(_LINHA_PROMOCAO.match(s)) or bool(_COMECA_COM_DATA.match(s)),
    )
    lidas: list[tuple[str, date]] = []
    for linha in linhas:
        m = _LINHA_PROMOCAO.match(linha)
        if not m:
            continue
        d = _data(m.group(3))
        if d:
            lidas.append((re.sub(r"\s+", " ", m.group(2)).strip(), d))
    if not lidas:
        return None, None
    if posto:
        alvo = posto.casefold()
        for p, d in lidas:
            if p.casefold() == alvo:
                return d, None
    return lidas[0][1], (
        f"Data de promoção lida da primeira linha da tabela ({lidas[0][0]}), "
        f"que não confere com o posto atual ({posto}). Verifique."
    )


def _data_praca(linhas_secao: list[str]) -> date | None:
    """Última data de praça: a maior da 1ª coluna.

    Pode haver mais de uma passagem (quem foi desligado e reincorporado — caso
    do ARTHUR). Só a data que ABRE a linha é a data de praça; as demais são
    desligamento e data do documento.
    """
    linhas = _linhas_da_tabela(linhas_secao, lambda s: bool(_COMECA_COM_DATA.match(s)))
    datas = [d for linha in linhas if (d := _data(linha))]
    return max(datas) if datas else None


# --- formato SiCaPEx ----------------------------------------------------------
def _busca(linhas: list[str], padrao: str) -> str | None:
    rx = re.compile(padrao)
    for linha in linhas:
        m = rx.search(linha)
        if m:
            return _limpo(m.group(1))
    return None


def _extrair_sicapex(paginas: list[list[str]]) -> FichaExtraida:
    p1 = paginas[0]
    todas = [linha for pag in paginas for linha in pag]
    f = FichaExtraida(formato="sicapex")

    f.nome_completo = _busca(p1, r"^Nome:\s*(.+)$")
    f.cpf = _digitos(_busca(p1, r"^CPF:\s*([\d.\-]+)"))
    f.data_nascimento = _data(_busca(p1, r"Dt Nasc:\s*(\d{2}/\d{2}/\d{4})"))
    # 'Nome' (sem dois-pontos) é o rótulo truncado de 'Nome de Guerra'.
    f.posto_texto = _busca(p1, r"^Posto/Grad:\s*(.+?)\s+Nome\b")
    f.nome_guerra = _busca(p1, r"^Posto/Grad:.*?\bNome\s+(\S+)")
    f.om_texto = _so_a_om(_busca(p1, r"OM/CODOM:\s*(.+)$"))
    f.identidade = _digitos(_busca(p1, r"^Idt\s+(\d+)"))

    f.data_praca = _data_praca(_secao(todas, re.compile(r"^Datas? de Praça$")))
    f.data_promocao, aviso = _promocao(
        _secao(todas, re.compile(r"^PROMOÇÕES$")), f.posto_texto)
    if aviso:
        f.avisos.append(aviso)
    return f


# --- formato SCGPE (grade por coordenada) -------------------------------------
def _palavras_em_linhas(page, tolerancia: float = 3.0) -> list[list[dict]]:
    """Palavras da página agrupadas em linhas visuais (por proximidade de topo)."""
    linhas: list[list[dict]] = []
    for w in sorted(page.extract_words(), key=lambda w: (round(w["top"], 1), w["x0"])):
        if linhas and abs(w["top"] - linhas[-1][0]["top"]) <= tolerancia:
            linhas[-1].append(w)
        else:
            linhas.append([w])
    return [sorted(linha, key=lambda w: w["x0"]) for linha in linhas]


def _acha_rotulo(linha: list[dict], rotulo: str) -> float | None:
    """x0 do rótulo na linha (o rótulo pode estar quebrado em várias palavras)."""
    alvo = rotulo.split()
    for i in range(len(linha) - len(alvo) + 1):
        if [w["text"] for w in linha[i: i + len(alvo)]] == alvo:
            return linha[i]["x0"]
    return None


def _valores_da_grade(linhas: list[list[dict]], rotulos: tuple[str, ...]) -> dict[str, str]:
    """Casa cada rótulo com o valor abaixo dele, pela faixa horizontal da coluna.

    A faixa vai do x0 do rótulo ao x0 do rótulo seguinte na mesma linha; assim
    um campo vazio não empurra o valor do vizinho para a coluna errada.
    """
    for i, linha in enumerate(linhas):
        if _acha_rotulo(linha, rotulos[0]) is None:
            continue
        # Rótulo que não casa (variação de grafia) apenas fica sem valor — não
        # descarta a linha inteira, senão um detalhe derruba os campos vizinhos.
        colunas = [(r, x) for r in rotulos if (x := _acha_rotulo(linha, r)) is not None]
        limites = [x for _, x in colunas] + [float("inf")]
        valores: dict[str, list[str]] = {r: [] for r, _ in colunas}
        # Valores podem ocupar mais de uma linha (nome longo quebra em duas).
        for seguinte in linhas[i + 1: i + 4]:
            if any(_acha_rotulo(seguinte, r) is not None for r in rotulos):
                break
            if _parece_linha_de_rotulos(seguinte):
                break
            for w in seguinte:
                for j, (rotulo, _) in enumerate(colunas):
                    if limites[j] - 2 <= w["x0"] < limites[j + 1] - 2:
                        valores[rotulo].append(w["text"])
                        break
        return {r: " ".join(v) for r, v in valores.items()}
    return {}


def _so_a_om(texto: str | None) -> str | None:
    """Isola a OM em 'Cmdo CMS - 023556 Dt Início 11/09/2023' (linha do SiCaPEx).

    O rótulo seguinte e o CODOM vêm colados na mesma linha; o cadastro guarda
    só a sigla (não há coluna de CODOM no modelo).
    """
    if not texto:
        return None
    s = re.split(r"\s+Dt\s", texto)[0]          # corta 'Dt Início ...'
    s = re.sub(r"\s*[-–]\s*\d+\s*$", "", s)     # corta o CODOM
    return _limpo(s)


def _parece_linha_de_rotulos(linha: list[dict]) -> bool:
    """Heurística de parada: linha cujas palavras terminam em ':' é cabeçalho."""
    return any(w["text"].endswith(":") for w in linha)


def _extrair_scgpe(pdf, paginas: list[list[str]]) -> FichaExtraida:
    linhas_p1 = _palavras_em_linhas(pdf.pages[0].filter(_sem_rotacao))
    grade: dict[str, str] = {}
    for rotulos in _GRADE_SCGPE:
        grade.update(_valores_da_grade(linhas_p1, rotulos))

    todas = [linha for pag in paginas for linha in pag]
    f = FichaExtraida(formato="scgpe")
    f.nome_completo = _limpo(grade.get("Nome Completo:"))
    f.cpf = _digitos(_limpo(grade.get("CPF")))
    f.data_nascimento = _data(grade.get("Data Nascimento:"))
    f.posto_texto = _limpo(grade.get("Posto Graduação:"))
    f.nome_guerra = _limpo(grade.get("Nome Guerra:"))
    f.identidade = _digitos(_limpo(grade.get("Identidade Militar:")))
    f.om_texto = _limpo(grade.get("OM Atual"))

    # A tabela da seção 'Data Praça' é mais confiável que a coluna da capa.
    f.data_praca = (_data_praca(_secao(todas, re.compile(r"^Data Praça Data Desligamento")))
                    or _data(grade.get("Última Data Praça:")))
    f.data_promocao, aviso = _promocao(
        _secao(todas, re.compile(r"^Promoções Sucessivas$")), f.posto_texto)
    if aviso:
        f.avisos.append(aviso)
    return f


# --- entrada pública ----------------------------------------------------------
def detectar_formato(primeira_pagina: list[str]) -> str:
    cabecalho = " ".join(primeira_pagina[:12])
    if "SiCaPEx" in cabecalho:
        return "sicapex"
    if "FICHA INDIVIDUAL" in cabecalho or "Sistema Corporativo de Gestão de Pessoal" in cabecalho:
        return "scgpe"
    raise FichaInvalida(
        "PDF não reconhecido como ficha (esperado 'FICHA CADASTRO - SiCaPEx' "
        "ou 'FICHA INDIVIDUAL' do SCGPE)."
    )


def extrair(origem: bytes | str | Path) -> FichaExtraida:
    """Lê a ficha e devolve o rascunho. Levanta FichaInvalida se não reconhecer."""
    fonte = io.BytesIO(origem) if isinstance(origem, bytes) else str(origem)
    try:
        with pdfplumber.open(fonte) as pdf:
            if not pdf.pages:
                raise FichaInvalida("PDF sem páginas.")
            paginas = _linhas_por_pagina(pdf)
            formato = detectar_formato(paginas[0])
            ficha = (_extrair_sicapex(paginas) if formato == "sicapex"
                     else _extrair_scgpe(pdf, paginas))
    except FichaInvalida:
        raise
    except Exception as e:  # pdfminer levanta várias exceções para arquivo corrompido
        raise FichaInvalida(f"Não foi possível ler o PDF: {e}") from e

    if not ficha.nome_completo:
        ficha.avisos.append("Nome completo não encontrado na ficha.")
    ficha.avisos.append(
        "Número de antiguidade não consta na ficha (regra 9.5) — preencha à mão para praças."
    )
    return ficha
