"""Entry-point: python -m app.seeds [ano_inicio ano_fim]."""
from __future__ import annotations

import sys

from app.database import SessionLocal
from app.seeds import _anos_padrao, run


def main(argv: list[str]) -> None:
    if len(argv) == 2:
        anos = range(int(argv[0]), int(argv[1]) + 1)
    elif not argv:
        anos = _anos_padrao()
    else:
        print("uso: python -m app.seeds [ano_inicio ano_fim]", file=sys.stderr)
        raise SystemExit(2)

    with SessionLocal() as db:
        resultado = run(db, anos)

    print("Seed concluído (linhas novas):")
    for nome, qtd in resultado.items():
        print(f"  {nome}: {qtd}")


if __name__ == "__main__":
    main(sys.argv[1:])
