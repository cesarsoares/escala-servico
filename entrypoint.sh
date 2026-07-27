#!/bin/sh
# Prepara o banco e sobe a aplicação. Idempotente: pode rodar a cada start.
set -e

echo ">> migrações (alembic upgrade head)"
alembic upgrade head

echo ">> dados de referência (círculos, postos/graduações, tipos, feriados)"
python -m app.seeds

# A carga da planilha do brigada NÃO roda aqui: é migração única de dados
# reais, feita à mão uma vez, com conferência (python -m app.seeds.planilha).
# O gestor inicial também é criado à mão:
#     docker compose exec app python -m app.seeds.usuario <login> "<nome>"

echo ">> aplicação"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
