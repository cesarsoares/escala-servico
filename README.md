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
```

Pronto — **não há mais nada a configurar**. Abra http://localhost:8000/gestao:
como o banco ainda não tem gestor, o sistema leva à tela de **primeiro acesso**,
onde se cria o login do sargenteante; em seguida o **assistente de instalação**
conduz a sequência (OM → graduações → OMs de origem → efetivo → escalas →
histórico). A tela de primeiro acesso se fecha sozinha assim que existe um
gestor.

A chave que assina a sessão é **gerada no primeiro boot** e guardada em
`dados/secret_key`, junto do banco: nenhuma instalação roda com chave conhecida
e ninguém precisa lembrar de defini-la. Para controlar o valor (por exemplo,
compartilhar a sessão entre instâncias), basta passar `SECRET_KEY` no ambiente.

Se a senha do único gestor se perder, o acesso se recria pela linha de comando:

```bash
docker compose exec app python -m app.seeds.usuario brigada "Sgt Brigada"
```

Sem Docker (usa SQLite por padrão, sem configurar nada):

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload      # http://localhost:8000
```

## Backup e troca de máquina

O sistema guarda sozinho **uma cópia por dia** em `dados/backups/`, mantendo as
últimas 7. Em **Configurações → Backup e restauração** o gestor baixa o backup
completo, baixa qualquer uma das cópias automáticas, restaura a partir de um
arquivo e exporta os dados em CSV — sem terminal.

As cópias automáticas ficam no mesmo disco: resolvem erro humano e troca de
máquina, **não** resolvem disco perdido. Guarde fora do servidor o arquivo que
a tela gera.

**Trocar de servidor:**

```bash
docker compose stop            # na máquina antiga
# copie a pasta dados/ inteira para a nova (banco + chave de sessão + cópias)
docker compose up -d           # na máquina nova
```

Tendo só o arquivo `.sqlite3`, suba o sistema na máquina nova e restaure pela
tela: `/gestao` leva ao primeiro acesso, que oferece **"restaure a partir dele"**
— não crie um gestor antes, a restauração o apagaria. Detalhe no
[manual](docs/manual/manual.md), seção *2.7 Trocar de máquina*.

⚠️ A máquina nova não pode rodar uma imagem **mais antiga** que a de origem: o
banco sobe de versão, nunca desce, e o backup é recusado.

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
