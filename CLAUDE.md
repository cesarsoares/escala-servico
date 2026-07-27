# CLAUDE.md — Contexto do projeto (Sistema de Escala de Serviço — QG do CMS)

> Este arquivo é a memória do projeto. Leia-o inteiro antes de agir.
> Ele resume o propósito, o estado atual, as regras de negócio e as convenções.
> A fonte da verdade das regras é `docs/Regras_Sistema_Escala_QG.md`.

## O que é

Sistema web para gerar e controlar as **escalas de serviço** de Organizações
Militares, substituindo a planilha atual do LibreOffice. Nasceu para o Quartel
General do Comando Militar do Sul, mas o escopo foi **ampliado para qualquer OM,
incluindo batalhões** — por isso as escalas não são fixas: o gestor as cria e
configura. Hoje o Sargento Brigada faz tudo à mão (rotação, folgas, dispensas,
férias, escalas concorrentes) — o sistema automatiza isso.

## Estado atual (importante)

- As **regras de negócio foram validadas pelo Sargento Brigada** (docs versão 2).
  O escopo foi ampliado do QG para qualquer OM; a lógica de regra segue isolada
  em `app/domain/` porque ainda pode receber ajustes pontuais.
- **O domínio JÁ FOI ATUALIZADO para as regras v2** e está testado
  (**26 testes passando**). Incorpora: **vários militares por dia (postos)** via
  `motor.proximos`, **folga mínima configurável por escala com piso rígido de
  24h** (`folga.py`), **concorrência** (via `ultimo_termino_por_militar`) e
  **janela de serviço configurável por escala** (`Escala.inicio_servico`/
  `duracao_horas`, com `inicio_em`/`termino_em`; default 08:00/24h).
- **Ainda são stubs vazios** (aguardam o modelo de dados fechado):
  `app/models/` (tabelas), `app/schemas/`, `app/api/`, `app/services/`,
  `app/pdf/` e as telas em `app/web/`.
- **Sistema NÃO é multitenant:** uma instalação por OM (TI local sobe a sua).
  Nada de separação por OM no banco.

**Próximo passo: o modelo de dados (`app/models/`) derivado das regras v2.**
Já resolvido no domínio: `Militar` ganhou **`numero_antiguidade`** (incorporação,
desempate das praças — regras 3.2.1/9.5, art. 17 §1º da Lei 6.880/80) e
**`data_nascimento`** (desempate final "mais velho = mais antigo", art. 17);
`comparar_antiguidade` ramifica para praças e o `POSTO_ORDEM` foi completado com
a escala hierárquica do art. 16 (generais → oficiais → praças especiais →
graduados → Cb → Sd). Pendente: (b) a camada de serviço é quem monta o
**`ultimo_termino_por_militar`** varrendo as escalas concorrentes.

> Decisão a levar ao Brigada: o art. 17 põe **data de nascimento** ANTES da
> decisão manual; hoje o código segue essa ordem (9.6 nascimento → 9.4 manual).
> Confirmar se o `docs/` deve citar o critério de nascimento explicitamente.

## Stack

Python 3.12 · FastAPI · **SQLite** (SQLAlchemy 2.0 + Alembic; PostgreSQL
suportado, ver abaixo) · Jinja2 · WeasyPrint (PDF) · pytest · Docker/compose.

## Como rodar e testar

```bash
# Testes (rodar sempre após mexer em app/domain/)
pytest

# Rodar a aplicação (sem Docker; usa SQLite por padrão)
uvicorn app.main:app --reload        # http://localhost:8000

# Com Docker: um container, banco em ./dados/escala.sqlite3.
# O entrypoint roda alembic upgrade + seeds de referência antes do uvicorn.
# (Validado em 2026-07-25 com Docker 29.6 no WSL2; a imagem sai com 502 MB —
#  as libs do WeasyPrint respondem pela maior parte.)
docker compose up --build
docker compose exec app python -m app.seeds.usuario brigada "Sgt Brigada"  # 1º gestor

# Em produção, defina a chave de sessão antes de subir (>= 32 bytes):
#   SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))") docker compose up -d

# Backup: copiar o arquivo (com o app parado, ou via .backup)
cp dados/escala.sqlite3 /destino/

# Alternativa com PostgreSQL
docker compose -f docker-compose.postgres.yml up --build
```

## Arquitetura (onde as coisas moram)

```
app/
  domain/     <- CORAÇÃO. Lógica pura da escala, sem framework nem banco.
                 É AQUI que ficam as regras. Mudança de regra = mudança aqui.
    models.py       Estruturas puras (Militar, Escala, Participacao, Cor...)
    calendario.py   classificar_dia -> preta/vermelha (seção 5)
    folga.py        respeita_folga_minima -> piso de 48h (seção 7)
    antiguidade.py  comparar_antiguidade -> desempate (seção 9)
    motor.py        fila_ordenada / proximo -> quem serve (seção 6)
  models/     <- Tabelas do banco (SQLAlchemy). STUB.
  schemas/    <- Contratos Pydantic. STUB.
  services/   <- Orquestração: rotação, permuta, auditoria, importação da ficha.
    ficha.py        Leitor da ficha individual em PDF (SiCaPEx e SCGPE). Puro.
    importacao.py   Reconcilia o que a ficha trouxe com as tabelas de referência.
    publicacao.py   Monta o documento da escala do mês (regra 12).
    configuracao.py Identificação da OM, referências editáveis e gestores.
    importacao_csv.py Carga do histórico de serviços (conferir -> confirmar).
  api/        <- Rotas HTTP.
  web/        <- Telas (Jinja + estáticos): consulta aberta + /gestao + /manual.
                 static/menu.js é o ÚNICO JavaScript, e é nosso (cortina de
                 escalas da consulta). Nada de biblioteca nem CDN.
  pdf/        <- Exportação PDF (WeasyPrint). STUB.
tests/        <- Testes do domínio (passando).
docs/         <- Regras completas (.md e .pdf). FONTE DA VERDADE.
  manual/     <- Manual de uso (.md), servido em /manual. Editável sem reiniciar.
```

## Banco: SQLite por padrão (decidido em 2026-07-25)

**SQLite é o banco de produção**; PostgreSQL segue suportado (só trocar a
`DATABASE_URL` — nada no código depende de recurso exclusivo de um dos dois).

Por quê: o perfil é **um escritor** (o gestor) e **muitos leitores** (consulta
aberta), com volume irrisório — 285 militares e 666 serviços ocupam 320 KB, e a
projeção é de alguns milhares de linhas por ano. Medido: escalar um ano inteiro
leva ~2,8 s e, com WAL, a consulta segue respondendo durante a escrita. O ganho
real é de deploy: **um container e um arquivo**, sem serviço de banco, sem
senha, e o backup é copiar `dados/escala.sqlite3`.

**Reavaliar se** aparecer exigência de SGBD corporativo, acesso ao banco por
fora (BI), ou a ideia de centralizar várias OMs numa instância (hoje a
arquitetura é uma instalação por OM).

`app/database.py` liga três PRAGMAs em toda conexão SQLite — sem eles o banco
não se comporta como produção:
- `foreign_keys=ON` — **o SQLite nasce com as FKs desligadas**; sem isso aceita
  serviço apontando para militar inexistente e ignora ON DELETE CASCADE;
- `journal_mode=WAL` — leitor não bloqueia escritor;
- `busy_timeout` — espera a escrita em curso em vez de estourar "database is locked".

O listener é registrado na classe `Engine`, então vale também para as engines
que os testes criam. Coberto por `tests/test_integridade_sqlite.py`.

## Schema do banco — quem manda em quê

- **Fonte da verdade executável:** `app/models/` + `alembic/versions/`. O banco
  nasce de `alembic upgrade head`, nunca de `create_all` nem do .sql.
- **`db/schema.sql` é documento de leitura** (ver o modelo inteiro de uma vez).
  Não é aplicado por ninguém; mantenha-o à mão ao mexer nos models.
- `tests/test_schema_sincronizado.py` guarda os dois: models × migrações (via
  autogenerate, equivale a `alembic check`) e models × `schema.sql` (tabelas e
  nomes de coluna).

## Convenção crucial

Toda função de domínio referencia o **número da regra** no docstring
(ex.: "regra 7.2"). Ao mexer na lógica, mantenha essa rastreabilidade
código ↔ documento. Se uma regra do `docs/` mudar, ajuste o código E o teste
correspondente, citando o número.

## Vocabulário do domínio (use estes termos)

- **Escala**: entidade **criada/excluída pelo gestor** (não é fixa). Carrega:
  cores em que roda, nº de postos, participantes, concorrentes, folga mínima,
  **janela de serviço (início + duração)**, vigência (ativa/extinta).
- **Escala preta**: serviços dos **dias úteis**.
- **Escala vermelha**: serviços de **sábados, domingos e feriados**.
- Cada escala roda as duas filas (preta e vermelha) em paralelo.
- **Posto**: cada **vaga** de uma escala num dia. Escala pode ter 1 posto
  (oficial) ou vários (guarda com 12+). Cuidado: "posto/graduação" (patente) é
  outra coisa — sempre escrever "posto/graduação" quando for patente.
- **Quarto**: subdivisão de um posto revezada por vários militares no mesmo dia.
  É detalhe operacional; **não muda a folga**.
- **Unidade de escalação = militar-dia**: quem serve no dia (mesmo um quarto)
  ganha a folga cheia.
- **Serviço**: turno que pertence ao **dia em que começa** (define a cor).
  Janela **configurável por escala**; default 08:00 por 24h, mas há escalas de
  outra janela (ex.: plantão 18:00→08:00). A folga é medida do término ao início.
- **Mais folgado**: quem serviu há mais tempo naquela cor = topo da fila. O motor
  pega os **N mais folgados disponíveis**, N = nº de postos do dia.
- **Folga de rotação**: natural, cai da fila. Varia por escala (nº de
  participantes e de postos).
- **Folga mínima (configurável por escala, piso rígido 24h)**: nenhum militar
  assume novo serviço antes de completar a folga mínima **da escala em que vai
  entrar**, contada do término do anterior, em **qualquer cor e qualquer escala
  concorrente**. Default sugerido 48h; nunca < 24h ("saiu no dia X, não assume no
  dia X").
- **Escalas concorrentes**: relação **explícita e simétrica** declarada pelo
  gestor; é o que faz as escalas conversarem via folga mínima.
- **Participação restrita a uma cor (regra 3.3.1)**: o participante concorre nas
  duas cores (padrão) ou **em uma só** — o militar cuja função o impede de servir
  em dia útil participa da escala e só entra na fila da vermelha. **Não confundir
  com a cor da ESCALA** (4.2/4.5, o Museu): ali a escala não roda na preta; aqui
  ela roda e é a **pessoa** que concorre em uma cor.
- **Impedimento**: dispensa/férias/curso/operação. O militar é **pulado** no
  período mas **mantém a vez** (a fila não muda de ordem).
- **Isenção permanente**: feita por **não-participação** na escala (não há campo
  de "função"; se não deve concorrer, não é participante).
- **Extinção da escala**: efetivo insuficiente para o piso → sistema avisa →
  gestor decide, tipicamente extinguindo a escala.
- **Antiguidade das praças**: soldado tem **número de incorporação** (dado pela
  OM, informado no cadastro) — é o desempate da graduação (regras 3.2.1/9.5).

## Regras que já estão fechadas (resumo — detalhe em docs/)

1. Cor do dia: útil=preta; sáb/dom/feriado=vermelha. O gestor pode declarar
   qualquer dia como vermelha (override), com observação. Feriados nacionais
   embutidos + gestor adiciona outros.
2. Fim de semana prolongado = sequência de dias vermelhos, N militares/dia (N=postos).
3. Escala é CRUD do gestor: nome, cores, nº de postos, participantes,
   concorrentes, folga mínima, vigência. Nada de escalas fixas no código.
4. Rotação: ordena pelo mais folgado; pega os **N primeiros disponíveis**
   (N=postos); desempata por antiguidade.
5. Desempate (mais moderno primeiro): posto/graduação (Cap antes de Maj) → data
   de promoção → data de praça → decisão manual do brigada por notas de curso.
   Para **praças**, o desempate da graduação é o **número de incorporação**.
6. Museu: escala **só vermelha** (fim de semana). Exemplo, não escala fixa.
7. Folga mínima configurável por escala, piso rígido 24h (default 48h);
   concorrência explícita e simétrica; aplica-se o piso da escala de destino.
8. Efetivo insuficiente p/ o piso → sistema avisa → gestor decide (tipicamente
   extingue a escala).
9. Trocas/permutas: **registro puro**, sem retribuição automática nem recálculo
   de folga; a folga segue **quem estava escalado**, nunca quem cobriu. Permuta é
   **negada** se ferir a folga mínima de quem vai cobrir.
10. Previsão (documento estático publicado pelo gestor) × motor dinâmico. O
    sistema **avisa** quando divergem; o gestor re-fecha manualmente.
11. Acesso: consulta **aberta** sem login; gestão por login/senha; **múltiplos
    gestores**. Histórico/auditoria de todas as alterações manuais.
12. Saída: **PDF simples** (sem replicar boletim/DIEx).
13. Deploy: container Docker, banco **SQLite** em arquivo (PostgreSQL opcional).
    **Uma instalação por OM** (não multitenant).

## Fora do escopo da v1

**Escala de representação** (designação de N militares para eventos pontuais,
tipo palestra) é **Fase 2**. Não é rotação, não tem preta/vermelha, não gera
folga. Único vínculo: quem está de serviço 24h no dia não vai. Não construir
agora.

## Importação da ficha individual (feito)

Cadastro de militar tem dois caminhos: digitar ou **importar a ficha em PDF**,
que apenas **pré-preenche o formulário** (nada é gravado; o operador confere).
Vale no cadastro novo e na **edição** — é assim que se completam os 285
militares que entraram pela planilha só com nome + posto + OM.

- Dois formatos, detectados pelo cabeçalho: **SiCaPEx** (rótulos truncados na
  linha, lidos por regex) e **SCGPE** (grade de colunas lida por **coordenada**;
  a marca d'água diagonal é removida filtrando os caracteres rotacionados).
- `numero_antiguidade` (regra 9.5) **não existe em nenhuma das duas fichas** e
  segue **sempre digitado à mão**. Confirmado pelo usuário em 2026-07-26: o
  formato da ficha é **o mesmo para praças e oficiais**, então não há amostra
  de praça a procurar — o operador informa o número no cadastro. O importador
  avisa isso em toda importação, e deve continuar avisando.
- O que não reconcilia com as tabelas de referência fica **em branco com aviso**,
  nunca chutado (o SCGPE traz a OM por extenso — "Comando do Comando Militar do
  Sul" não casa com a sigla "Cmdo CMS").
- Depende de **pdfplumber** (leitura); `weasyprint` só escreve PDF.

`fichas_exemplo/` contém fichas reais com **dados pessoais** (CPF, identidade,
filiação, endereço, dados bancários). Já está no `.gitignore` e no
`.dockerignore` — a imagem vai para o TI da OM, os dados não. O mesmo vale para
a planilha do brigada e para o `escala.sqlite3`: **os dados chegam pela carga,
nunca embutidos na imagem** (conferido: `/app` na imagem não tem nenhum deles).

## Saída impressa da escala (feito — regra 12)

`GET /escalas/{id}/impressao?ano=&mes=` — **aberta** (regra 13.1), é o documento
publicado. Template `impressao.html` + `static/impressao.css`, com bloco
`@media print`: o navegador imprime ou salva em PDF. Link na tela do calendário.

- **Preto e branco em primeiro lugar:** dia vermelho é marcado pela letra **V**
  (e `*` para feriado), não só por fundo — impressora monocromática perde cor.
- Havendo **permuta**, o documento mostra o escalado E quem cobre; não troca um
  pelo outro, porque a folga continua sendo do escalado (regra 9).
- Só entram os dias COM serviço (escala só-vermelha não imprime linhas vazias);
  a coluna "Posto" some quando a escala tem uma vaga só.
- **WeasyPrint ainda não está no caminho** (nem instalado neste ambiente): quando
  entrar no container, renderiza ESTE mesmo template — sem retrabalho.

## Telas de permuta (feito — regra 9)

`/gestao/permutas` (mês de uma escala: quem serve, o que já foi coberto) e
`/gestao/permutas/servico/{id}` (form da troca). Duas telas de propósito: um
select de substitutos em cada linha do mês geraria milhares de `<option>`.

- A recusa (regra 10.5) chega ao gestor como **mensagem com o motivo** — negar é
  informação, não erro de sistema.
- O substituto sai dos **participantes ativos da escala** (menos o escalado).
- Cancelar a permuta faz o escalado voltar a figurar; tudo auditado, com
  `autorizado_por` vindo do gestor logado.
- Datas em português vêm de `publicacao.DIAS_SEMANA`/`MESES` — `strftime('%a')`
  usa o locale do sistema e imprimiria "Mon".

## Code-review de 2026-07-25 — os 7 achados foram corrigidos (26/07)

**220 testes passando.** As regressões estão em `tests/test_review_fixes_0725.py`
(uma por achado, com o número no docstring). O que mudou, e o que vale lembrar:

1. **"Regravar" apagava permutas em silêncio** (CASCADE do `Servico`). Agora
   `_permutas_do_periodo` fotografa ANTES do delete: a tela lista o que foi
   perdido e a auditoria grava em `permutas_apagadas`. Depois do delete não há
   como saber — a permuta some sem rastro.
2. **`/gestao/permutas`** valida mês/ano com `Query(ge=…, le=…)`.
3. **`ano` na consulta aberta** (`/` e `/escalas/{id}/impressao`) tem faixa
   `ANO_MIN..ANO_MAX` (em `app/web/__init__.py`, junto dos templates: main.py e
   web/gestao.py precisam dos dois sem import circular).
4. **`importacao.rascunho(db, ficha, militar_id)`** — na edição o próprio
   militar não conta como duplicata (identidade, CPF e homônimo).
5. **CPF/identidade só em dígitos** por `app/normalizacao.so_digitos`, usado
   pelas TRÊS portas: ficha (`ficha._digitos`), API (validator em
   `schemas/militar.py`) e formulário (`_ler_form`). Digitado sem nenhum dígito
   é erro, não None.
6. **`ResultadoDia.servicos_gravados`** é preenchido por `gravar_dia`; a tela
   anuncia o que gravou (0 ao re-escalar sem "regravar") e mostra o previsto
   entre parênteses quando diferem.
7. **O campo de arquivo da ficha migrou para DENTRO do formulário principal**
   (`formaction`/`formenctype`/`formnovalidate` no botão). Assim o que já foi
   digitado — inclusive o nº de antiguidade, que a ficha nunca traz — vai junto
   e volta preenchido. Por isso o `arquivo` é opcional na rota: clicar sem
   escolher o PDF vira mensagem, não 422.

Sem achados em `services/ficha.py`, `publicacao.documento`, nos PRAGMAs de
`database.py`, no `.dockerignore` e em `security.py`.

## Telas de escala e de calendário (feito — regras 4, 2.5, 3.3, 7.4.1, 5)

`app/web/gestao_escalas.py` (router separado de `gestao.py` só por tamanho,
mesmo prefixo `/gestao`; espelha o corte da API). Fecham pela interface o que
antes só existia na API JSON — o gestor não precisa mais de `curl` para operar.

- `/gestao/escalas` lista (ativas, `?extintas=1` inclui as extintas) com cores,
  postos, participantes e serviços gravados. `/gestao/escalas/nova` cria a
  escala **já com seus postos**; `/gestao/escalas/{id}` é a tela de trabalho:
  dados, postos, participantes e concorrentes numa página só.
- **Posto com serviço gravado não pode ser removido** — apagá-lo levaria junto
  quem serviu ali e a folga que daí decorre. O caminho é extinguir a escala
  (regra 8). Idem o último posto: escala sem posto não escala ninguém.
- **Isentar** um participante desativa o vínculo, não o apaga (regra 7.6), e
  reincluir reaproveita o mesmo vínculo em vez de duplicar.
- Concorrência é **simétrica**: declarada de um lado, aparece nos dois. Declarar
  de novo não gera auditoria falsa (mesma guarda da API).
- `/gestao/calendario?ano=` cobre feriados da OM (regra 5.2) e a cor forçada do
  dia (5.3) — **nos dois sentidos**, inclusive feriado trabalhado virando preta.
- Erro de validação **devolve o que foi digitado**, não os valores do banco.
- Medido no banco real: a escala maior (139 participantes) renderiza em ~63 ms.

## Painel do gestor (feito — 2026-07-26)

`app/services/painel.py` alimenta `/gestao`. A lógica fica no serviço (testável
sem HTTP) e **nenhum bloco reimplementa regra**: ou lê o que está gravado, ou
pergunta ao motor. A ordem na tela é deliberada — o que faz alguém deixar de
entrar de serviço vem antes de qualquer contagem.

1. **Cobertura** — até quando cada escala está fechada e quantos dias da janela
   de 30 estão descobertos. Só conta o dia que a escala **realmente roda**: a
   só-vermelha não acusa buraco em dia útil (regra 4.5).
2. **Exige atenção** — (a) militar **escalado E impedido** no mesmo dia, que é o
   que aparece quando a dispensa é lançada depois de fechar o mês (a saída é
   re-escalar com *regravar*); (b) dia gravado com menos militares que postos
   (7.8); (c) escala com menos participantes que vagas.
3. **Hoje / amanhã** com a permuta ao lado do escalado (a folga é do escalado —
   regra 9). Se amanhã não está fechado, mostra **quem o motor escalaria**, via
   `rotacao.escalar_dia` sem gravar. Isso só é calculado quando é exibido.
4. **Cadastro do efetivo** — quantos faltam com promoção (9.2), nascimento
   (art. 17) e **praças sem número de antiguidade (9.5)**. Não é capricho: sem
   esses campos o desempate da fila roda sem os critérios que a regra manda.
5. **Distribuição no ano** — mínimo, máximo e quantos nunca serviram, por
   escala. Quem nunca serviu conta como zero no cálculo da diferença, senão o
   número mentiria.

`rotacao.escala_roda_cor` passou a ser pública porque o painel também precisa
dela. Medido no container: painel em 150–200 ms com os dados reais.

**O painel provou o próprio valor no primeiro uso:** com os dados reais, mostrou
que **julho inteiro estava sem escala fechada** (a planilha trouxe o *estado da
fila*, não serviços; agosto foi escalado, julho não) e que **73 das 73 praças
estão sem número de antiguidade**.

## Gestão da pessoa concentrada (feito — 2026-07-26)

No Efetivo, cada militar ativo tem o atalho **"impedimentos"** ao lado de
"desativar". Ele abre `/gestao/impedimentos?militar_id=<id>`, que **não é** a
lista geral: é a ficha de impedimentos daquela pessoa — formulário já apontando
para ela e lista só com os dela. Numa OM de 285 militares, cair na lista geral
obrigaria a procurar, que é justamente o que o atalho existe para evitar.

- O campo oculto `contexto` carrega de quem é a tela: depois de gravar ou
  remover, volta-se para a ficha da pessoa; sem contexto, para a lista geral.
  Erro de validação também preserva o foco.
- A lista ganhou a coluna **Situação** (em curso / futuro / encerrado): é o que
  o gestor olha antes de escalar, e a data crua obrigava a fazer a conta.
- `?militar_id=` de militar inexistente cai na lista geral, sem erro.

## Tela de histórico / auditoria (feito — regra 11)

`app/web/gestao_auditoria.py` + `gestao/auditoria.html`, em `/gestao/auditoria`.
O dado já era gravado em toda mutação e a API `/api/auditoria` já existia; o que
faltava era onde o gestor lesse (o painel só mostra as 8 últimas).

- Filtros por entidade, ação, gestor e período; paginação de 50. A página pede
  **51 registros** para saber se há "seguintes" sem um `COUNT` que varra a tabela
  inteira (ela cresce sem teto).
- O que a tela acrescenta à API: `auditoria.diferencas(antes, depois)` mostra
  **só o campo que mudou** (`nome_guerra: (BASE)DE FREITAS → DE FREITAS`), em vez
  de dois JSON de 12 campos para comparar a olho. Em criar/excluir um dos lados
  não existe, então abre o retrato inteiro num `<details>`.
- Filtro de data ilegível é **ignorado**, não vira 500.

**⚠️ O banco grava em UTC.** `auditoria.criado_em` vem de `func.now()`, que no
SQLite é UTC e sem tzinfo. A tela exibia o valor cru — todo horário aparecia
**3h adiantado**, o que num registro de "quem mexeu e quando" é defeito. Agora
existe o filtro Jinja `hora_local` (em `app/web/__init__.py`), usado no painel e
no histórico; `astimezone()` sem argumento usa o fuso do servidor e não depende
do pacote `tzdata` (que o Windows não traz). No container, o fuso vem do `TZ` do
compose — e por isso o `tzdata` entrou no Dockerfile: **sem ele o TZ é ignorado
em silêncio** e a imagem volta a mostrar UTC.

## A cor da escala vermelha (definida pela OM — 2026-07-26)

**C:0 M:100 Y:100 K:0**, ou seja vermelho puro `#FF0000`, no token
`--vermelha-escala`. Vale no **panorama mensal da consulta**: o dia de escala
vermelha tem a célula inteira nessa cor, com número e nomes em **branco e
seminegrito** (sobre vermelho puro o contraste do branco é 4:1 — é a gordura da
fonte que sustenta a leitura).

- Alerta, erro e perigo continuam no vermelho escuro `--vermelho`; são coisas
  diferentes e não devem se confundir com a cor da escala.
- Dia de **outro mês** não recebe o vermelho forte, mesmo sendo fim de semana:
  a grade ganharia dois blocos que não são do mês em vista.
- O contorno de "hoje" vira branco quando cai num dia vermelho.
- A amostra da legenda usa a mesma cor — senão não é legenda.
- **A folha de impressão (`impressao.css`) NÃO usa esta cor**, de propósito: ali
  o dia vermelho é marcado pela letra **V** e por fundo cinza, porque a
  impressora da OM pode ser monocromática (regra 12). Mudar isso é decisão
  separada, e custa toner colorido.

## Gráficos nas telas de gestão (feito — 2026-07-26)

Segunda entrega de design, e a primeira que **mexe em marcação**: sete telas
ganharam gráficos, todos em `<div>`/`<span>` com CSS — **sem biblioteca**. A
folha nova é `static/graficos.css`, carregada só onde há gráfico, via
`{% block estilos %}` no `base.html` (vem depois da principal, cujos tokens
reusa).

**As larguras são DADO, não enfeite** — barra errada mente com aparência de
precisão. Toda a aritmética ficou em `services/painel.py`, testada em
`tests/test_graficos.py`, e o template só formata:

| Tela | Gráfico | De onde vem o número |
|---|---|---|
| painel, escalas | cobertura coberto × a fechar | `Cobertura.pct_coberto` |
| painel | completude do cadastro | `SaudeCadastro.campos` → `CampoCadastro` |
| painel | amplitude da distribuição | `Equidade.pct_piso/pct_amplitude` |
| escala | **Fila** — ranking por serviços | `fila_por_servicos` |
| impedimentos | linha do tempo | `linha_do_tempo` |
| permutas | fluxo do mês | dias com serviço, no próprio template |
| histórico | resumo por ação/entidade | agregação em `gestao_auditoria` |

Decisões que o desenho não podia tomar sozinho:
- **Quem nunca serviu puxa o piso da amplitude para zero** (`Equidade.piso`),
  senão a barra esconderia exatamente o pior caso.
- **Barra de largura mínima (6%) para quem tem zero serviços** — zero invisível
  não comunica "este é o próximo".
- A **linha do tempo só mostra o que ainda importa** (em curso e futuros, no
  máximo 15): impedimento encerrado não muda escalação e o histórico inteiro
  vira mancha. O que não cabe continua na tabela, e a tela diz quantos são.
- A **Fila é leitura de equidade, não previsão do motor** — e o texto da tela diz
  isso. Quem entra amanhã sai da rotação (regra 6), que pesa folga, cor,
  impedimento e antiguidade.
- Escala sem dia na janela tem cobertura **100%**, não 0% — a só-vermelha numa
  semana sem fim de semana não está "descoberta".

**Armadilha do exportador:** `tools/exportar_interface.py` reescreve os caminhos
de `/static/*.css`. Ao surgir a folha nova, ela não estava na lista e as telas
exportadas abriram **sem gráfico nenhum** — parecia defeito da aplicação. A lista
agora é a constante `FOLHAS`; ao acrescentar uma folha, acrescente ali também.

## Usabilidade da gestão (feito — 2026-07-26)

Três defeitos que o uso real expõe e que **nenhum CSS resolvia**. Testes em
`tests/test_gestao_usabilidade.py`.

1. **Busca no Efetivo** (`?q=`, `?posto_graduacao_id=`, `?om_id=`). Casa nome de
   guerra **ou** nome completo, sem diferenciar maiúsculas — quem procura digita
   "souza". A tela diz "2 de 285", e o total nunca mente sobre o efetivo. Filtro
   inválido é ignorado, não vira 500.
2. **Confirmação de ação.** Toda ação de gestão termina em redirecionamento
   (POST-redirect-GET); o efeito colateral era o sistema **nunca dizer que deu
   certo** — só o erro tinha mensagem. Agora a confirmação viaja como
   `?ok=<chave>` e é traduzida pelo dicionário `AVISOS` em `app/web/__init__.py`.
   Sem sessão, sem cookie, sem estado no servidor: a mesma URL mostra o mesmo
   aviso. **Chave desconhecida não exibe nada** — a URL não injeta texto na tela.
   Um teste garante que toda chave usada nas rotas tem tradução.
3. **`<select>` do efetivo agrupado por posto/graduação** (`agrupar_por_posto`),
   em ordem **hierárquica** — alfabética não quer dizer nada aqui. Vale para
   impedimento, permuta e participante de escala.

Também: o painel ganhou a faixa **"Precisa de você agora"** (o urgente não pode
ter o mesmo peso visual da estatística do ano), e a tela da escala ganhou um
índice das quatro seções com a contagem de cada uma.

## Linguagem visual "1c / Claro" (aplicada — 2026-07-26)

`style.css` foi substituído por uma reescrita externa (entrega de design), que
**mantém todos os nomes de classe**: nenhum template mudou por causa dela. Fundo
azulado frio, calendário em blocos arredondados, navegação em pílulas, números e
horários em fonte monoespaçada de sistema.

Conferido antes de aplicar, e é o que se deve conferir em qualquer entrega
futura: `impressao.css` intocado, **nenhuma dependência externa** (sem CDN nem
fonte remota — a rede da OM pode não ter internet), `--vermelha-escala:#ff0000`
usado só no dia do calendário, anel de foco preservado, e o conjunto de
seletores comparado com o anterior (só se perdeu um, que o novo layout tornou
obsoleto). O comparador de seletores é a defesa contra a troca de folha inteira
levar embora a regra de um componente que o designer não viu.

Dois ajustes meus por cima da entrega:
- **A navegação da gestão não cabia**: oito abas mais o bloco da direita jogavam
  o "Sair" para uma segunda linha. Padding reduzido de 14 para 11 px.
- **O crachá do cabeçalho vinha com "QG" fixo no CSS.** Sigla não é conteúdo de
  folha de estilo, e o sistema roda em **qualquer OM, inclusive batalhão**.
  Agora vem de `settings.om_sigla`/`om_nome` (`.env`), exposto a todos os
  templates como global do Jinja em `app/web/__init__.py`. O `<title>` e o `<h1>`
  também deixaram de ter "QG do CMS" no código.

## Passe de visual (feito — 2026-07-26)

`static/style.css` foi reescrito com **tokens em `:root`** (tinta, superfícies,
institucional, semânticas, forma). Mexer na aparência agora é mexer no token, não
caçar hexadecimal solto. Nenhum nome de classe mudou — os templates só ganharam
o estado "ativo" na navegação. A folha de impressão (`impressao.css`) é outra e
não foi tocada.

Princípios anotados no topo do arquivo, na ordem em que valem: legibilidade antes
de enfeite; **cor nunca é a única informação** (a pilula/tag traz a palavra
escrita, como no documento impresso); densidade média, porque são tabelas de 139
linhas.

O que mudou de fato:
- **Anel de foco** (`:focus-visible`) — não existia. Quem navega por teclado ou
  leitor de tela não enxergava onde estava.
- **Aba marcada** na navegação da gestão e no topo — dava para clicar errado.
- Hierarquia de botão (primário/secundário/perigo) e o `Salvar` que deixou de
  ocupar a largura toda, o que o fazia parecer a ação da página inteira.
- Tabela larga rola dentro de si em telas estreitas, em vez de esticar a página.
- `prefers-reduced-motion` desliga as transições.

**Como conferir sem servidor:** o Edge do Windows tira screenshot por linha de
comando (`--headless=new --screenshot`). O jeito que funcionou foi capturar as
páginas autenticadas com o `TestClient` para .html no scratchpad (trocando
`/static/style.css` por uma cópia local) e renderizar esses arquivos. Página
grande precisa de `--virtual-time-budget=4000` e `Start-Process -Wait`, senão o
PNG não chega a ser escrito e você lê o screenshot antigo achando que o CSS não
pegou.

## Configurações da instalação (feito — 2026-07-26, tarde)

`/gestao/configuracao` (router `app/web/gestao_config.py`, lógica em
`app/services/configuracao.py`). É o que faltava para o sistema ser **instalável
por qualquer OM sem editar código, `.env` ou banco**.

**É um hub de cartões**, e cada assunto tem a sua página
(`templates/gestao/config/`). A primeira versão era uma página só com âncoras e
não se sustentou: a seção de graduações tem 17 linhas **com formulário embutido
em cada uma** e enterrava as outras quatro.

Três decisões do hub que não são estéticas:
- **O cartão traz a contagem E a pendência** (`configuracao.panorama`, testado
  sem HTTP). Hub que só repete títulos custa um clique e não devolve nada; com
  a pendência, a página se paga: "OM não definida", "só um gestor ativo — se
  perder a senha só a TI recria", "nenhum serviço registrado".
- **Ícones em SVG embutido** (`config/_icones.html`): sem CDN e sem fonte de
  ícones, porque a rede da OM pode não ter internet; e sem emoji, que sai
  diferente em cada máquina. Herdam a cor por `currentColor`.
- **O cartão inteiro é um `<a>`** — nada de `div` com `onclick`: funciona no
  teclado e no leitor de tela, e o clique pega a área toda.
- Gravar volta para a **própria seção**, não para o hub: quem cadastrou uma OM
  normalmente vai cadastrar a próxima.

Os cartões estão na **ordem de instalação** (a mesma do manual), porque cada
passo depende do anterior.

1. **OM da instalação** — `organizacao_militar.propria`. Antes vinha de
   `om_sigla`/`om_nome` no `.env`, que passou a ser só **reserva do primeiro
   boot**. Marcar uma desmarca as outras (regra 13.2).
2. **OMs** — as de origem de quem serve aqui (regra 3.2). Não exclui OM com
   militar nem a OM da casa.
3. **Postos e graduações** — a mudança de peso. Ver a seção abaixo.
4. **Tipos de impedimento** — variam muito por OM; desativa em vez de apagar.
5. **Gestores** (regra 11) — fecha a última lacuna que só existia no CLI.
   Nunca exclui (a auditoria referencia `usuario_id`); não deixa desativar o
   **próprio acesso** nem o **último gestor ativo**. `snapshot()` já excluía
   `senha_hash`, e um teste garante que a senha não vaza para a auditoria.

Também: `configuracao` (chave/valor) guarda o **contato do suporte**, que
aparece no rodapé. Chave fora de `CHAVES` é recusada — a tela não grava
qualquer coisa no banco.

**A identificação da OM chega aos templates por dependência GLOBAL**
(`main.identificar_om` → `request.state` → context processor em `web/__init__`),
não por middleware: assim usa a **mesma sessão do pedido** e obedece ao
`dependency_overrides` dos testes. Middleware abriria sessão própria contra o
banco em arquivo e quebraria a suíte inteira.

## ⚠️ A ordem hierárquica agora é DADO, não constante

Consequência de tornar as graduações editáveis, e a mudança mais delicada
desta rodada:

- `domain/models.Militar` ganhou **`posto_ordem`** e **`posto_eh_praca`**;
- `antiguidade._ordem_posto`/`_eh_praca` usam o que o chamador informou e só
  caem no `POSTO_ORDEM`/`GRADUACOES_PRACA` embutidos **quando vêm None** (é o
  que mantém os testes de domínio montando `Militar` à mão);
- **`mapeamento.militar_para_dominio` é quem leva a ordem do banco** — e por
  isso a consulta carrega também `posto_graduacao.circulo`.

Se alguém voltar a tratar `POSTO_ORDEM` como fonte da verdade, uma OM que
reordene a tabela passa a desempatar a fila com a ordem antiga, **em silêncio**.
Coberto por `test_a_ordem_editada_e_a_que_o_desempate_usa`.

`posto_graduacao.ordem_hierarquica` **deixou de ser UNIQUE**: mover duas linhas
de lugar numa transação só é impossível com a checagem imediata do SQLite. Quem
impede empate é `configuracao.renumerar_graduacoes` (10, 20, 30...), chamada
depois de toda mudança. A tela move com ↑/↓, não digitando número — o gestor
pensa em hierarquia, não em "ordem 23".

## ⚠️ Migração no SQLite com dados: FKs precisam sair do caminho

Descoberto ao migrar o banco real (285 militares): `batch_alter_table`
**recria a tabela** (copia, dropa, renomeia), e com `foreign_keys=ON` — que
`app/database.py` liga em toda conexão — o DROP da tabela-pai falha assim que
existe uma linha filha. Acrescentar uma coluna em `organizacao_militar`
quebrava por causa de `militar.om_id`.

`alembic/env.py` agora desliga as FKs durante a migração e roda
`PRAGMA foreign_key_check` antes de religar. **O PRAGMA vai no cursor CRU**: em
transação ele é ignorado em silêncio, e qualquer execução pelo SQLAlchemy abre
uma. Foi exatamente o que aconteceu na primeira tentativa — migração com
`exit=0`, sem efeito, e a tabela temporária do batch largada no banco.

Se uma migração falhar no meio, limpe as `_alembic_tmp_*` antes de repetir: no
SQLite o DDL não é transacional e o que já rodou fica.

## Importação do histórico de serviços em CSV (feito — regra 6/7)

`/gestao/importar` + `app/services/importacao_csv.py`. É a carga de uma OM que
instala hoje mas já tem passado em planilha: **sem o histórico o motor começa
com todo mundo empatado em "nunca serviu"** e a primeira escalação sai só pela
antiguidade, ignorando quem acabou de deixar o serviço.

- **Duas etapas** (conferir → confirmar), como na ficha em PDF. Ler não grava.
  O conteúdo volta num campo oculto — sem estado no servidor, o que se confirma
  é o que se conferiu. A **releitura** na etapa 2 é deliberada: entre uma e
  outra alguém pode ter fechado o dia pela tela de escalação.
- **Nada é chutado**: escala/militar/data/posto que não casam viram recusa
  **com o motivo e o número da linha**. Homônimo em duas OMs sem a coluna `om`
  é ambiguidade — recusa, não palpite.
- ⚠️ **Militar que está no CSV mas não está no efetivo é RECUSA, e isso foi
  decidido assim** (usuário, 2026-07-27) — não é lacuna a consertar. A recusa é
  por linha: as boas entram, e depois de cadastrar quem faltava **basta
  reimportar o mesmo arquivo**, porque serviço já gravado é recusado como
  duplicado (`(posto, dia)`), sem duplicar nada. Criar o militar a partir do
  arquivo foi **descartado**: o CSV traz nome e, no máximo, a sigla da OM —
  faltam posto/graduação (que ordena a fila e sai no documento impresso) e o
  número de antiguidade das praças. O militar entraria na rotação com o
  desempate errado e sem patente na escala publicada: erro silencioso, pior que
  a recusa.
- **Serviço importado é fato consumado**: entra mesmo que o militar não seja
  mais participante (isso é AVISO). Recusar impediria de carregar exatamente o
  histórico que se quer registrar.
- A **cor sai do calendário** (feriados e overrides), nunca do arquivo — cor é
  consequência da data.
- **Excel pt-BR**: `;` + cp1252. O leitor tenta utf-8(-sig) e cai em cp1252.
  O **"baixar modelo"** sai preenchido com as escalas e o efetivo reais — sem
  ele ninguém acerta o cabeçalho de primeira.

## Manual de uso (feito — `/manual`)

Markdown em `docs/manual/manual.md`, renderizado por `app/web/manual.py`
(dependência `markdown`, já em `requirements.txt`). **Aberto**, porque explica
também a consulta, que é aberta (regra 13.1).

- **Organizado por tarefa, não por tela** ("fechar o mês", "lançar uma
  dispensa", "o militar saiu da OM"). Manual por tela é o que ninguém lê.
- Fonte única: o mesmo arquivo se lê no repositório e na tela, e **o texto
  editado vale sem reiniciar** (cache por mtime) — corrigir uma frase em
  produção não pode exigir derrubar o servidor.
- O índice sai dos próprios títulos (extensão `toc`, `toc_depth 2-3`): seção
  nova aparece sem ninguém lembrar do sumário.
- Usa a folha do **documento** (`impressao.css` + `manual.css`), não a do
  sistema: é feito para ser impresso, e misturar as duas só daria conflito.
  Na impressão cada assunto começa em página nova e o índice some.
- Um teste garante que o manual **não cita "QG do CMS"** — ele serve a qualquer OM.

## Ajustes de 2026-07-26 (tarde) — os 9 pedidos do usuário

Regressões em `tests/test_ajustes_0726.py`. **426 testes passando.**

1. **Botão ilegível em `/gestao/permutas`** não era escolha de cor: `.tabela td a`
   é mais específico que `.botao` e apagava a letra branca — azul-escuro sobre
   azul-escuro, contraste 1,3:1. Consertado na raiz (`.tabela td a.botao`) e o
   "permutar" virou `mini secundario`, como as ações de linha das outras telas.
7. **Posto/graduação na consulta** (a impressão já trazia, via
   `publicacao._nome`). Sigla em corpo menor: a célula do dia comporta 12 postos.
8. **"Ver inativos" e "ver ativos" mostravam a mesma lista** — `inativos=1` não
   filtrava pelos inativos, só removia o filtro dos ativos. Agora são três
   estados (`situacao=ativos|inativos|todos`); `inativos=1` segue valendo como
   "todos", que é o que sempre fez.
9. **Tabelas**: zebra e hover em **tons do azul institucional** (cinza de hover
   se confundia com a faixa par), cabeçalho mais escuro, e negrito **só** no
   cabeçalho e na primeira coluna — negrito em tudo vira mancha em 139 linhas.
   Ordem obrigatória na folha: **zebra < estado da linha < hover**, senão a
   linha permutada caindo na faixa par deixa de parecer permutada.
   `impressao.css` ficou fora (toner).

O **rodapé** agora traz sistema, versão (`app.VERSAO`, fonte única, usada também
no título da API), modo de acesso, link do manual e o contato do suporte.

**A barra de navegação da gestão tem largura fixa e já estourou duas vezes.**
Com a aba Configurações foi preciso apertar o padding (11→9px), encurtar
"Escalar período" → "Escalar" e **não** pôr o Manual ali (ele está no rodapé de
todas as telas). Cada aba nova custa espaço de todas.

## Participação por cor + cortina de escalas (feito — 2026-07-27)

Dois pedidos anotados pelo usuário no `notas.txt`. Testes em
`tests/test_participacao_por_cor.py`.

**1. Regra 3.3.1 — participante que só concorre numa cor.** Escrita no `docs/`
(portanto **pendente de validação do Brigada**, como as outras duas em aberto).
`participacao` ganhou `serve_preta`/`serve_vermelha` (default TRUE, `CHECK` de
ao menos uma), migração `e2b6d1a7c9f3`. O filtro entra num ponto só —
`motor.fila_ordenada` —, e a diferença que importa está no docstring: quem não
concorre na cor **fica fora da fila**, não é "pulado" como no impedimento
(6.4); ele nunca teve vez ali. **A folga mínima não muda**: sai de uma fila, não
fica disponível a qualquer hora na outra.

- A escolha ficou **no vínculo, não na pessoa** (decisão do usuário): é onde o
  gestor já administra participantes, e o mesmo militar pode ter arranjos
  diferentes em escalas diferentes.
- A tela só pergunta a cor quando a **escala roda as duas** — no Museu a
  pergunta não teria resposta.
- Valor desconhecido no form cai em **ambas**, nunca em "nenhuma": a URL não
  pode fabricar participante que não concorre em cor alguma.
- **O alerta de efetivo curto do painel virou por cor** (7.8): somar as duas
  esconderia exatamente o buraco que a restrição cria. Faltando o mesmo nas
  duas, sai **um aviso só** — aí é falta de gente, não restrição de cor.
- A **Fila** da tela da escala marca "só vermelha": barra curta sem explicação
  parece injustiça na leitura de equidade.
- O **CSV de histórico avisa** (não recusa) quando o serviço importado é de cor
  que o militar hoje não concorre — fato consumado, mas costuma denunciar
  arquivo trocado.

**2. Menu lateral em cortina na consulta.** Primeira versão saiu em
`<details>` sem JS; o usuário pediu a **cortina lateral deslizante** (mandou um
HTML de referência), e é o que está no ar: `aside.menu-escalas` fixo fora da
tela, aba com o rótulo **ESCALAS** na **altura do meio** da janela, lista
vertical com o nome de cada escala. Vale só para a consulta — a gestão tem a
lista própria em `/gestao/escalas`.

- **`static/menu.js` é o ÚNICO JavaScript do sistema**, e é nosso: sem
  biblioteca, sem CDN (a rede da OM pode não ter internet). Carregado só na
  consulta, pelo `{% block cabeca %}` novo do `base.html` — **no `<head>` e sem
  `defer`**, de propósito (ver abaixo).
- **A cortina fica aberta até alguém fechar.** Escolher uma escala **recarrega a
  página**, então o estado vive no `localStorage` e é reposto como classe
  `cortina-aberta` no `<html>` **antes do primeiro traço na tela** — daí o
  script no `<head>`. Com `defer`, o menu piscaria fechado a cada troca de mês.
- **Aberta, ela EMPURRA a página** (`padding-left` no `body`) em vez de tapá-la:
  ficando aberta o tempo todo, cobrir a primeira coluna esconderia o domingo,
  que é dia de escala vermelha. Abaixo de 900px volta a sobrepor.
- **A lista rola sozinha** (`max-height:calc(100vh - 90px)`), com `overflow-x`
  travado — `overflow-y:auto` liga o eixo x junto e aparecia uma barra
  horizontal. Título e puxador ficam fora da rolagem. Conferido com 30 escalas.
- **O `<noscript>` do template devolve a lista ao topo da página.** A consulta é
  aberta (13.1): sem script, o visitante não pode ficar sem as outras escalas.
- **`overflow` no menu recorta o puxador**, que por definição vive fora dele — e
  aí não há como abrir a cortina. A rolagem fica na LISTA. Foi assim que a
  primeira captura saiu sem botão nenhum.
- A aba traz a **palavra escrita**, não só a seta: é o único ponto de entrada.
- Com a cortina fechada, **quem diz de qual escala é o mês é o título**
  (`.de-qual-escala`) — antes era o chip marcado. Sem isso, a cortina troca
  poluição por desorientação.
- `tools/exportar_interface.py`: a constante virou **`ESTATICOS`** e inclui o
  `menu.js`. Sem isso a tela exportada abre sem a cortina — o mesmo tropeço que
  a folha `graficos.css` já tinha causado.

## ⚠️ Existem DOIS bancos nesta máquina

Descoberto ao migrar em 27/07 e fácil de tropeçar de novo:

- `./escala.sqlite3` (raiz) é o do **desenvolvimento local** — é o que
  `app/config.py` usa por padrão (`sqlite:///./escala.sqlite3`) e o que o
  `alembic` da linha de comando migra;
- `./dados/escala.sqlite3` é o **do container** (volume do compose), migrado
  pelo entrypoint.

Os dois têm os 285 militares reais, mas **não** o mesmo número de serviços.
`alembic current` dizendo `head` enquanto `dados/escala.sqlite3` segue na
revisão antiga não é bug: são bancos diferentes.

## Próximos passos sugeridos

1. **WeasyPrint gerando o PDF pelo servidor** — já está instalado na imagem
   (entrou junto com as libs do Pango); falta a rota que renderiza
   `impressao.html` (e agora também `manual.html`) com ele, em vez de depender
   do "salvar como PDF" do navegador.
2. Validar com o Brigada as mudanças de regra em aberto: a remoção da previsão
   (afeta a regra 10), o override para **preta** (generaliza a 5.3) e a
   **participação restrita a uma cor (3.3.1)**, escrita em 27/07.
3. Assistente de primeira execução: hoje Configurações + importação já dão conta
   de instalar numa OM nova, mas o caminho precisa ser descoberto pelo gestor.
4. Fase 2: módulo de representação.

## ⚠️ O container não fica de pé nesta máquina (Windows/WSL)

Descoberto em 2026-07-26: o `escala-app-1` **sai sozinho com `Exited (0)`** cerca
de um minuto depois de subir, quando a sessão do WSL termina — a distro encerra e
leva o daemon (e todo container) junto. Os outros containers da máquina mostram o
mesmo padrão. Não é defeito da aplicação: o log mostra shutdown limpo.

- Para **medir ou testar**, faça subir e usar **na mesma invocação do WSL**
  (um script só), senão o app já morreu quando o curl chega.
- `restart: unless-stopped` entrou no compose — resolve queda do daemon e
  reinício do servidor (é o certo para a OM), mas **não** impede a parada quando
  o próprio WSL encerra.
- No servidor da OM (Linux com Docker) isso não acontece.
- Para uso contínuo neste Windows, o caminho é Docker Desktop iniciando com o
  sistema, ou simplesmente rodar `uvicorn` local durante os testes.

## Estado do container (2026-07-26)

Reconstruído com o código de hoje e conferido pelo WSL: consulta aberta,
impressão e as oito telas de gestão respondem 200 entre 22 e 58 ms, com os dados
reais. O fuso dentro do container é `-03` e a auditoria mostra o horário
convertido. **Docker só responde pelo WSL** (`wsl -e bash <script.sh>`); passar
comandos inline pelo PowerShell come as aspas.

## Estilo

- Comentários e nomes de domínio em **português** (consistente com o time).
- Mantenha o domínio livre de dependências de framework/banco (testável isolado).
- Rode `pytest` após qualquer mudança em `app/domain/`.
