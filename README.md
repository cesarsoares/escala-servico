# Sistema de Escala de Serviço — QG do CMS

Sistema para gerar e controlar as escalas de serviço do Quartel General,
substituindo a planilha atual do LibreOffice.

> **Estado:** estrutura inicial (esqueleto). As regras de negócio estão
> documentadas em `docs/` e aguardam validação do Sargento Brigada antes da
> implementação completa. O núcleo de domínio (`app/domain/`) já traz as
> regras que estão fechadas, com testes.

## Stack

- **Python 3.12** + **FastAPI** (API e telas)
- **PostgreSQL** (dados) via SQLAlchemy 2.0 + Alembic (migrações)
- **Jinja2** para as telas (consulta aberta + gestão)
- **WeasyPrint** para exportar PDF
- **Docker / docker-compose** para rodar em qualquer máquina
- **pytest** para testar o motor de escala

## Arquitetura em camadas

```
app/
  domain/     <- CORAÇÃO. Lógica pura da escala, sem framework nem banco.
                 É aqui que moram as regras (rotação, folga, antiguidade).
                 Totalmente testável de forma isolada.
  models/     <- Persistência (tabelas SQLAlchemy).
  schemas/    <- Contratos de entrada/saída (Pydantic).
  services/   <- Orquestração (previsão, trocas, exportação de PDF).
  api/        <- Rotas HTTP.
  web/        <- Telas (templates Jinja + estáticos).
  pdf/        <- Geração de PDF.
```

A separação existe para um motivo: quando o brigada corrigir uma regra
("regra 7.3 está errada"), a mudança fica **localizada em `app/domain/`**,
sem espalhar pelo resto do sistema.

## Como rodar (desenvolvimento)

Com Docker (recomendado):

```bash
docker compose up --build
```

A aplicação sobe em http://localhost:8000

Sem Docker (usa SQLite por padrão, sem configurar nada):

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Testes

```bash
pytest
```

## Documentação das regras

- `docs/Regras_Sistema_Escala_QG.md` — regras completas (fonte da verdade)
- `docs/Regras_Sistema_Escala_QG.pdf` — mesma coisa, para leitura/impressão

Cada função do domínio referencia o número da regra correspondente
(ex.: "regra 7.2") no docstring, para rastrear código <-> regra.

## Próximos passos

1. Validar `docs/` com o Sargento Brigada.
2. Fechar o modelo de dados (tabelas).
3. Implementar persistência, API e telas sobre o domínio já testado.
4. Fase 2: módulo de representação.
