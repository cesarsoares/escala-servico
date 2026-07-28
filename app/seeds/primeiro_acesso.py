"""Anuncia a senha de primeiro acesso no arranque, se ainda não houver gestor.

    python -m app.seeds.primeiro_acesso

Chamado pelo `entrypoint.sh` (Docker) e pelo `windows/escala.cmd`, sempre antes
de subir o servidor. É o único momento em que a TI está olhando a saída — e é
por isso que o anúncio mora aqui, e não num evento de startup da aplicação:
dentro do FastAPI, ele custaria uma consulta ao banco a cada boot e apareceria
no log de quem instalou há meses.

Numa instalação que já tem gestor não imprime nada e apaga o arquivo da senha,
caso tenha sobrado de uma tentativa anterior.
"""
from app.database import SessionLocal
from app.services.instalacao import anunciar_primeiro_acesso


def main() -> None:
    with SessionLocal() as db:
        anunciar_primeiro_acesso(db)


if __name__ == "__main__":
    main()
