# Migrações (Alembic)

As migrações do banco ficam aqui. Serão geradas após definir os modelos
em `app/models/`. Comando típico:

    alembic revision --autogenerate -m "descricao"
    alembic upgrade head
