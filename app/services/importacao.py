"""Reconciliação do rascunho da ficha com o banco (regra 3.2).

Traduz o que `app.services.ficha` leu do PDF para os valores do formulário de
cadastro: texto de posto/graduação e de OM viram IDs das tabelas de referência.
Nada é gravado — o retorno alimenta o formulário, o operador confere e salva
pelo caminho normal.

Campo que não resolve fica em branco COM AVISO, nunca chutado: escolher a OM
errada é pior do que pedir que o operador selecione.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.services.ficha import FichaExtraida


def _chave(texto: str | None) -> str:
    """Forma comparável: sem acento, sem pontuação, minúscula, espaço único."""
    if not texto:
        return ""
    s = unicodedata.normalize("NFKD", texto)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip().casefold()


def _resolver_posto(db: Session, texto: str | None) -> int | None:
    if not texto:
        return None
    # '1º Ten – F' (forma feminina) é o mesmo posto de '1º Ten'.
    alvo = _chave(re.sub(r"\s*[–-]\s*F$", "", texto.strip()))
    for p in db.scalars(select(PostoGraduacao)):
        if _chave(p.sigla) == alvo or _chave(p.nome) == alvo:
            return p.id
    return None


def _resolver_om(db: Session, texto: str | None) -> int | None:
    if not texto:
        return None
    alvo = _chave(texto)
    for o in db.scalars(select(OrganizacaoMilitar)):
        if _chave(o.sigla) == alvo or _chave(o.nome) == alvo:
            return o.id
    return None


def _duplicatas(db: Session, ficha: FichaExtraida, militar_id: int | None = None) -> list[str]:
    """Avisa se o militar já parece cadastrado, para não criar um duplicado.

    Na EDIÇÃO (`militar_id`), o próprio militar não conta: completar pela ficha
    quem entrou pela planilha é justamente o fluxo previsto, e acusá-lo de
    duplicata de si mesmo só assusta o operador.
    """
    avisos = []
    for campo, valor in (("identidade", ficha.identidade), ("CPF", ficha.cpf)):
        if not valor:
            continue
        coluna = Militar.identidade if campo == "identidade" else Militar.cpf
        ja = db.scalar(select(Militar).where(coluna == valor))
        if ja and ja.id != militar_id:
            avisos.append(
                f"{campo} {valor} já pertence a {ja.nome_guerra} (#{ja.id})"
                f"{' — inativo' if not ja.ativo else ''}. "
                "Edite o militar existente em vez de criar outro."
            )
            return avisos      # identificação positiva; o resto é ruído

    # O efetivo carregado da planilha entrou só com nome + posto + OM, sem
    # identidade nem CPF — ali o único indício de que a pessoa já existe é o
    # nome de guerra.
    if ficha.nome_guerra:
        alvo = _chave(ficha.nome_guerra)
        homonimos = [m for m in db.scalars(select(Militar))
                     if _chave(m.nome_guerra) == alvo and m.id != militar_id]
        if homonimos:
            quem = ", ".join(f"{m.nome_guerra} (#{m.id})" for m in homonimos[:5])
            avisos.append(
                f"Já existe militar com este nome de guerra: {quem}. "
                "Se for a mesma pessoa, edite o cadastro dela para completar os "
                "dados em vez de criar outro."
            )
    return avisos


def rascunho(db: Session, ficha: FichaExtraida,
             militar_id: int | None = None) -> tuple[dict, list[str]]:
    """Campos do militar (tipados; ausente = None) + avisos para o operador.

    `militar_id` é o militar sendo EDITADO — ele não conta como duplicata.
    """
    avisos = list(ficha.avisos)

    posto_id = _resolver_posto(db, ficha.posto_texto)
    if ficha.posto_texto and posto_id is None:
        avisos.append(f"Posto/graduação '{ficha.posto_texto}' não encontrado — selecione.")
    elif not ficha.posto_texto:
        avisos.append("Posto/graduação não encontrado na ficha — selecione.")

    om_id = _resolver_om(db, ficha.om_texto)
    if ficha.om_texto and om_id is None:
        avisos.append(
            f"OM '{ficha.om_texto}' não corresponde a nenhuma cadastrada — selecione "
            "(o SCGPE traz o nome por extenso, o cadastro usa a sigla)."
        )
    elif not ficha.om_texto:
        avisos.append("OM não encontrada na ficha — selecione.")

    avisos.extend(_duplicatas(db, ficha, militar_id))

    valores = {
        "nome_guerra": ficha.nome_guerra,
        "nome_completo": ficha.nome_completo,
        "posto_graduacao_id": posto_id,
        "om_id": om_id,
        "identidade": ficha.identidade,
        "cpf": ficha.cpf,
        "data_promocao": ficha.data_promocao,
        "data_praca": ficha.data_praca,
        "data_nascimento": ficha.data_nascimento,
        "numero_antiguidade": None,   # não consta na ficha (regra 9.5)
    }
    return valores, avisos


def para_formulario(valores: dict) -> dict:
    """Converte o rascunho tipado nos valores de texto que o form HTML espera."""
    def texto(v):
        if v is None:
            return ""
        return v.isoformat() if hasattr(v, "isoformat") else str(v)

    return {campo: texto(valor) for campo, valor in valores.items()}
