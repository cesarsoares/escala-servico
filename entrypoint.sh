#!/bin/sh
# Prepara o banco e sobe a aplicação. Idempotente: pode rodar a cada start.
set -e

echo ">> migrações (alembic upgrade head)"
alembic upgrade head

echo ">> dados de referência (círculos, postos/graduações, tipos, feriados)"
python -m app.seeds

echo ">> primeiro acesso"
python -m app.seeds.primeiro_acesso

# A carga da planilha do brigada NÃO roda aqui: é migração única de dados
# reais, feita à mão uma vez, com conferência (python -m app.seeds.planilha).
#
# O PRIMEIRO GESTOR é criado pela própria tela: com o banco sem nenhum usuário,
# /gestao leva a /gestao/primeiro-acesso, que pede a SENHA DE INSTALAÇÃO impressa
# logo acima (e guardada em /dados/primeiro-acesso.txt) e se fecha assim que
# existe gestor. O comando abaixo continua valendo como socorro (senha perdida),
# e é o único caminho quando já há gestor:
#     docker compose exec app python -m app.seeds.usuario <login> "<nome>"

echo ">> aplicação"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
