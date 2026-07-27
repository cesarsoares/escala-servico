"""Bootstrap de gestor (regra 11) — fora da API, pois a gestão é protegida.

Cria (ou atualiza a senha de) um gestor a partir da linha de comando. É o modo
de a TI local semear o primeiro login numa instalação nova.

    python -m app.seeds.usuario brigada "Sgt Brigada"
    python -m app.seeds.usuario brigada "Sgt Brigada" --senha trocar-depois

Sem --senha, a senha é pedida interativamente (não ecoa nem fica no histórico).
"""
from __future__ import annotations

import argparse
import getpass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.gestao import Usuario
from app.security import hash_senha


def criar_ou_atualizar_gestor(db: Session, login: str, senha: str, nome: str) -> Usuario:
    """Idempotente: cria o gestor ou atualiza nome/senha se o login já existe."""
    usuario = db.scalar(select(Usuario).where(Usuario.login == login))
    if usuario is None:
        usuario = Usuario(login=login, nome=nome, senha_hash=hash_senha(senha), ativo=True)
        db.add(usuario)
    else:
        usuario.nome = nome
        usuario.senha_hash = hash_senha(senha)
        usuario.ativo = True
    db.commit()
    db.refresh(usuario)
    return usuario


def main() -> None:
    parser = argparse.ArgumentParser(description="Cria/atualiza um gestor da escala.")
    parser.add_argument("login")
    parser.add_argument("nome")
    parser.add_argument("--senha", help="se omitido, é pedida interativamente")
    args = parser.parse_args()

    senha = args.senha or getpass.getpass("Senha do gestor: ")
    if len(senha) < 8:
        parser.error("a senha precisa de ao menos 8 caracteres")

    db = SessionLocal()
    try:
        u = criar_ou_atualizar_gestor(db, args.login, senha, args.nome)
        print(f"gestor '{u.login}' (id={u.id}) pronto.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
