"""Recuperação de senha do gestor (demanda do Brigada, 30/07).

"O Brigada esqueceu a senha que colocou no momento da instalação." Não há
e-mail: o sistema roda na OM, isolado, e pode nem ter internet (regra 13.3). Não
há pergunta secreta: seria uma segunda senha, tão esquecível quanto a primeira.

**Metade do problema já tinha solução e ninguém sabia:** um gestor logado troca a
senha de outro em Configurações → Gestores (`configuracao.trocar_senha`). Isso
cobre a OM com dois gestores — e é por isso que o assistente de instalação insiste
no segundo. O que faltava era o caso do **gestor único**, que é o do Brigada:
ninguém logado para resetar, e a porta fechada.

A prova de autoridade aqui é a mesma do primeiro acesso: **acesso ao servidor**.
Pedir a recuperação grava um código aleatório em `dados/recuperar-senha.txt`, ao
lado do banco; quem instalou (ou a TI da OM) lê o arquivo e o digita. Quem só
alcança a porta 8000 pela rede não tem como lê-lo.

Duas diferenças deliberadas em relação à senha de primeiro acesso:

  - **este código VENCE** (`recuperacao_validade_min`, 60 min por padrão). Aquele
    morre sozinho quando o gestor é criado; este não teria fim natural nenhum, e
    um arquivo esquecido na pasta seria uma segunda porta permanente;
  - **pedir de novo dentro da validade devolve o MESMO código**, não um novo.
    Sem isso, qualquer um na rede recarregando a página invalidaria o código que
    a TI acabou de ler ao telefone — negação de serviço contra o dono da máquina.

O CLI (`python -m app.seeds.usuario`) continua existindo e continua sendo o
socorro final: ele não depende nem do servidor estar de pé.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.gestao import Usuario
from app.security import hash_senha
# A regra da senha (tamanho mínimo, as duas iguais) é a MESMA da tela de
# gestores; duplicá-la aqui deixaria as duas portas com exigências diferentes.
from app.services.configuracao import ErroConfiguracao, _validar_senha

log = logging.getLogger(__name__)

_CABECALHO = (
    "# Codigo de RECUPERACAO DE SENHA do Sistema de Escala de Servico.\n"
    "#\n"
    "# Alguem pediu para redefinir a senha de um gestor. Se foi voce, use o\n"
    "# codigo abaixo na tela /gestao/recuperar-senha. Ele vale por pouco tempo\n"
    "# (veja 'vence_em') e deixa de servir assim que for usado.\n"
    "#\n"
    "# Se NAO foi voce, apague este arquivo: ninguem consegue redefinir senha\n"
    "# nenhuma sem ele.\n"
)

# ASCII no arquivo de propósito: ele é lido no console do servidor, e o console
# do Windows usa cp850 — acento sairia ilegível, como no log do escala.cmd.


@dataclass(frozen=True)
class Codigo:
    """O código vigente e até quando vale."""
    valor: str
    vence_em: datetime
    caminho: Path
    novo: bool          # foi criado agora, ou já existia e ainda valia?


def _arquivo() -> Path:
    return Path(settings.recuperacao_file)


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _ler() -> tuple[str, datetime] | None:
    """Código gravado e seu vencimento; None se não há, está ilegível ou venceu.

    Arquivo corrompido é tratado como ausente: o pedido seguinte grava outro. A
    alternativa — estourar — deixaria o dono da máquina sem caminho nenhum.
    """
    arquivo = _arquivo()
    if not arquivo.is_file():
        return None
    try:
        corpo = arquivo.read_text(encoding="utf-8")
        dados = json.loads(corpo[corpo.index("{"):])
        valor = str(dados["codigo"])
        vence = datetime.fromisoformat(dados["vence_em"])
    except (OSError, ValueError, KeyError):
        return None
    if vence <= _agora():
        return None
    return valor, vence


def pedir(db: Session) -> Codigo:
    """Gera (ou devolve) o código de recuperação desta instalação.

    Não diz nada sobre QUEM tem conta: a tela pede o login só na hora de trocar,
    e a recusa por login inexistente vem depois do código conferido. Pedir o
    código não revela nada a quem está na rede — o valor só existe no arquivo.
    """
    vigente = _ler()
    if vigente is not None:
        valor, vence = vigente
        return Codigo(valor, vence, _arquivo(), novo=False)

    valor = secrets.token_urlsafe(9)
    vence = _agora() + timedelta(minutes=settings.recuperacao_validade_min)
    arquivo = _arquivo()
    corpo = _CABECALHO + json.dumps(
        {"codigo": valor, "vence_em": vence.isoformat(),
         "gestores_ativos": db.scalar(
             select(func.count()).select_from(Usuario).where(Usuario.ativo.is_(True))) or 0},
        indent=2,
    ) + "\n"
    try:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(corpo, encoding="utf-8")
        os.chmod(arquivo, 0o600)          # no Windows o efeito é parcial
    except OSError:
        # Sem onde gravar, o código não serve para nada: ninguém consegue LÊ-LO.
        # Vai para o log, que é onde a TI ainda pode alcançá-lo.
        log.warning("sem onde gravar o codigo de recuperacao (%s); ele vale so "
                    "para este processo: %s", arquivo, valor)
    return Codigo(valor, vence, arquivo, novo=True)


def conferir(digitado: str) -> bool:
    """Compara em tempo constante — `==` vaza o tamanho do prefixo acertado."""
    vigente = _ler()
    if vigente is None:
        return False
    return secrets.compare_digest((digitado or "").strip(), vigente[0])


def encerrar() -> None:
    """Apaga o código. Chamado depois de usar — é credencial viva."""
    try:
        _arquivo().unlink(missing_ok=True)
    except OSError:
        pass


class RecuperacaoNegada(Exception):
    """Recuperação recusada. Carrega o motivo, que vai para a tela."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


def redefinir(db: Session, codigo: str, login: str, senha: str, repetida: str) -> Usuario:
    """Troca a senha de um gestor mediante o código do arquivo.

    Reativa o gestor desativado de propósito: a OM que desativou o único acesso
    por engano ficaria de fora de outra forma, e quem tem o código já provou o
    acesso ao servidor — que é mais do que o gestor comum tem.
    """
    if not conferir(codigo):
        raise RecuperacaoNegada(
            "Código inválido ou vencido. Peça um novo e leia o arquivo no servidor.")

    usuario = db.scalar(select(Usuario).where(Usuario.login == (login or "").strip()))
    if usuario is None:
        raise RecuperacaoNegada("Não há gestor com esse login.")

    try:
        _validar_senha(senha, repetida)
    except ErroConfiguracao as e:
        raise RecuperacaoNegada(str(e)) from e

    usuario.senha_hash = hash_senha(senha)
    usuario.ativo = True
    db.flush()
    encerrar()          # usado uma vez, e só uma
    return usuario
