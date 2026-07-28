# Sistema de Escala de Serviço

Gera e controla as **escalas de serviço** de uma Organização Militar — quem
entra de serviço em cada dia, respeitando rotação, folga mínima, impedimentos e
antiguidade —, substituindo a planilha do LibreOffice mantida à mão.

Nasceu para o Quartel General do Comando Militar do Sul, mas **serve a qualquer
OM, inclusive batalhão**: as escalas não são fixas no código, o gestor as cria e
configura, e a identificação da OM, os postos/graduações e os tipos de
impedimento são editáveis pela própria interface.

> **Uma instalação por OM.** O sistema não é multitenant: cada OM sobe a sua,
> com o seu banco. É um container e um arquivo.

## Estado

Em uso com dados reais. A consulta aberta, o documento impresso e as telas de
gestão (efetivo, escalas, calendário, impedimentos, permutas, escalação,
histórico, configurações e importação do histórico) estão funcionando, com
**461 testes** cobrindo o domínio, os serviços e as telas.

As regras foram validadas pelo Sargento Brigada; três mudanças posteriores
aguardam nova validação — estão listadas em `CLAUDE.md`.

## Acesso

- **Consulta aberta**, sem login: o calendário do mês e a versão para impressão.
- **Gestão com login**, com múltiplos gestores e auditoria de toda alteração.
- **Manual de uso** em `/manual`, escrito por tarefa ("fechar o mês", "lançar
  uma dispensa"), servido a partir de `docs/manual/manual.md`.

## Stack

- **Python 3.12** + **FastAPI** (API e telas)
- **SQLite** como banco de produção, via SQLAlchemy 2.0 + Alembic
  (PostgreSQL é suportado — basta trocar a `DATABASE_URL`)
- **Jinja2** nas telas; **WeasyPrint** para PDF
- **Docker / compose** para subir em qualquer máquina
- **pytest**

Nenhuma dependência de CDN, fonte remota ou biblioteca de front-end: a rede da
OM pode não ter internet.

## Como rodar

Com Docker (recomendado). O entrypoint aplica as migrações e os seeds antes de
subir o servidor:

```bash
docker compose up --build
docker compose exec app python -m app.seeds.usuario brigada "Sgt Brigada"  # 1º gestor
```

Em produção, defina a chave de sessão antes de subir (>= 32 bytes):

```bash
SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))") docker compose up -d
```

Sem Docker (usa SQLite por padrão, sem configurar nada):

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload      # http://localhost:8000
```

**Backup** é copiar o arquivo do banco (`dados/escala.sqlite3`).

## Testes

```bash
pytest
```

## Arquitetura

```
app/
  domain/     <- CORAÇÃO. Lógica pura da escala, sem framework nem banco.
                 É aqui que moram as regras (rotação, folga, antiguidade).
                 Testável de forma isolada.
  models/     <- Tabelas (SQLAlchemy). O banco nasce das migrações Alembic.
  schemas/    <- Contratos de entrada/saída (Pydantic).
  services/   <- Orquestração: rotação, permutas, publicação, importações.
  api/        <- Rotas HTTP (JSON).
  web/        <- Telas (Jinja + estáticos): consulta aberta, /gestao e /manual.
  pdf/        <- Exportação por WeasyPrint (hoje a impressão sai pelo navegador,
                 a partir do mesmo template).
```

A separação existe por um motivo prático: quando o brigada corrigir uma regra
("a 7.3 está errada"), a mudança fica **localizada em `app/domain/`**, sem
espalhar pelo resto do sistema.

## Documentação das regras

- `docs/Regras_Sistema_Escala_QG.md` — regras completas (**fonte da verdade**)
- `docs/Regras_Sistema_Escala_QG.pdf` — mesma coisa, para leitura e impressão
- `db/schema.sql` — o modelo de dados inteiro numa página, para leitura
- `CLAUDE.md` — memória do projeto: decisões, armadilhas e o porquê de cada uma

Cada função do domínio cita o número da regra correspondente no docstring
(ex.: "regra 7.2"), para manter rastreável a ligação código ↔ documento.

## Próximos passos

1. Gerar o PDF pelo servidor com WeasyPrint (a lib já está na imagem).
2. Validar com o Sargento Brigada as três mudanças de regra em aberto.
3. Assistente de primeira execução, conduzindo a instalação numa OM nova.
4. Fase 2: módulo de escala de representação.
