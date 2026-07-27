"""Gerador de PDF mínimo para os testes do leitor de ficha.

Escreve texto em coordenadas dadas (e, opcionalmente, rotacionado — é assim que
se reproduz a marca d'água do SCGPE). Evita depender de gerador de PDF nos
testes e, principalmente, evita levar fichas reais (dados pessoais) para dentro
da suíte: cada teste monta a ficha de que precisa.
"""
from __future__ import annotations

import math

# (x, y, texto, rotacionado?) — y cresce para cima, como no PDF.
Item = tuple[float, float, str, bool]

LARGURA, ALTURA = 595, 842   # A4 em pontos


def _escapa(texto: str) -> str:
    return texto.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _stream(itens: list[Item], tamanho: float) -> bytes:
    partes = ["BT", f"/F1 {tamanho} Tf"]
    for x, y, texto, girado in itens:
        if girado:
            c, s = math.cos(math.radians(35)), math.sin(math.radians(35))
            partes.append(f"1 0 0 1 0 0 Tm {c:.4f} {s:.4f} {-s:.4f} {c:.4f} {x} {y} Tm")
        else:
            partes.append(f"1 0 0 1 {x} {y} Tm")
        partes.append(f"({_escapa(texto)}) Tj")
    partes.append("ET")
    return "\n".join(partes).encode("latin-1", errors="replace")


def pdf(paginas: list[list[Item]], tamanho: float = 9) -> bytes:
    """Monta um PDF de uma ou mais páginas com os itens de texto informados."""
    objetos: list[bytes] = []

    def add(corpo: bytes) -> int:
        objetos.append(corpo)
        return len(objetos)          # números de objeto começam em 1

    fonte = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                b"/Encoding /WinAnsiEncoding >>")
    n_paginas = len(paginas)
    # Reserva: catálogo, árvore de páginas e, por página, o objeto Page + conteúdo.
    id_catalogo, id_arvore = fonte + 1, fonte + 2
    ids_pagina = [id_arvore + 1 + 2 * i for i in range(n_paginas)]

    add(b"<< /Type /Catalog /Pages %d 0 R >>" % id_arvore)
    filhos = b" ".join(b"%d 0 R" % i for i in ids_pagina)
    add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (filhos, n_paginas))

    for i, itens in enumerate(paginas):
        id_conteudo = ids_pagina[i] + 1
        add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (id_arvore, LARGURA, ALTURA, fonte, id_conteudo))
        dados = _stream(itens, tamanho)
        add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(dados), dados))

    saida = bytearray(b"%PDF-1.4\n")
    posicoes = []
    for numero, corpo in enumerate(objetos, start=1):
        posicoes.append(len(saida))
        saida += b"%d 0 obj\n%s\nendobj\n" % (numero, corpo)

    inicio_xref = len(saida)
    saida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    for pos in posicoes:
        saida += b"%010d 00000 n \n" % pos
    saida += (b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
              % (len(objetos) + 1, id_catalogo, inicio_xref))
    return bytes(saida)


def linhas(x: float, y_inicial: float, textos: list[str], passo: float = 14) -> list[Item]:
    """Bloco de linhas empilhadas a partir de (x, y_inicial), de cima para baixo."""
    return [(x, y_inicial - i * passo, t, False) for i, t in enumerate(textos)]


def colunas(y: float, celulas: list[tuple[float, str]]) -> list[Item]:
    """Uma linha da grade: cada célula no seu x (é assim que o SCGPE alinha)."""
    return [(x, y, texto, False) for x, texto in celulas]
