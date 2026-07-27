-- =============================================================================
-- Sistema de Escala de Serviço — QG do CMS
-- Schema PostgreSQL — DOCUMENTO DE REFERÊNCIA (para ler o modelo de uma vez).
--
-- ATENÇÃO: a fonte da verdade executável são os models em `app/models/` e as
-- migrações em `alembic/versions/`. É o Alembic que cria o banco:
--     alembic upgrade head
-- Este arquivo NÃO é aplicado por ninguém; ao mexer nos models, atualize-o
-- junto (o teste `test_schema_sincronizado.py` só garante models↔migrações).
-- Sincronizado com os models em 2026-07-25.
--
-- Convenções desta revisão (decididas com o usuário em 2026-07-23):
--   * IDs em INT/SERIAL (não BIGINT) — escala de uma OM não justifica bigint.
--   * `posto_graduacao` (ex-"graduacao"); "é praça" derivado de `circulo_hierarquico`.
--   * `circulo_hierarquico` é entidade própria (6 círculos legais, art. 16).
--   * Sem tabela de previsão: previsão = SELECT/view -> PDF reimprimível.
--   * `cor` como ENUM. Datas de fila derivadas de `servico` (sem coluna-cache).
--   * `permuta` ancora em `servico`. Quarto NÃO é modelado (detalhe operacional).
--
-- Rastreabilidade: comentários citam o número da regra (docs/Regras_..._v2).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Tipos
-- -----------------------------------------------------------------------------
CREATE TYPE cor AS ENUM ('preta', 'vermelha');   -- regras 2.5 / 2.6


-- =============================================================================
-- TABELAS DE REFERÊNCIA (seed fixo / gerenciadas pelo gestor)
-- =============================================================================

-- Organização Militar de origem do militar (regra 3.2). Uma instalação por OM,
-- mas o militar pode ser de OM diferente da anfitriã -> vira referência.
-- `propria` marca a OM DONA da instalação (cabeçalho/rodapé das telas). Só uma
-- linha pode tê-la; quem garante é services/configuracao.py.
CREATE TABLE organizacao_militar (
    id       INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nome     VARCHAR(120) NOT NULL,
    sigla    VARCHAR(30)  NOT NULL UNIQUE,
    propria  BOOLEAN      NOT NULL DEFAULT FALSE
);

-- Círculo hierárquico (Lei 6.880/80, art. 16). Fixo por lei -> seed.
-- `eh_praca` responde a regra 9.5 (desempate por nº de antiguidade das praças).
CREATE TABLE circulo_hierarquico (
    id        INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nome      VARCHAR(60) NOT NULL UNIQUE,
    ordem     INT         NOT NULL UNIQUE,  -- maior = mais antigo
    eh_praca  BOOLEAN     NOT NULL
);

-- Posto ou graduação (patente). Ordem do art. 16 (maior = mais antigo/graduado).
-- Editável pelo gestor: a OM pode não ter uma graduação ou acrescentar outra.
-- `ordem_hierarquica` NÃO é UNIQUE — só a ordem relativa importa, e a checagem
-- imediata do SQLite impediria trocar duas linhas de lugar numa transação só.
-- `ativo=FALSE` esconde a graduação dos formulários sem quebrar a FK do militar.
CREATE TABLE posto_graduacao (
    id                INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sigla             VARCHAR(12) NOT NULL UNIQUE,   -- ex.: 'Cap', '2º Sgt', 'Sd'
    nome              VARCHAR(60) NOT NULL,
    ordem_hierarquica INT         NOT NULL,          -- art. 16 (maior = mais antigo)
    circulo_id        INT         NOT NULL REFERENCES circulo_hierarquico (id),
    ativo             BOOLEAN     NOT NULL DEFAULT TRUE
);

-- Tipo de impedimento: dispensa, férias, curso, operação... (regra 7.5).
-- Varia por OM -> editável em Configurações; desativa em vez de apagar.
CREATE TABLE tipo_impedimento (
    id    INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nome  VARCHAR(60) NOT NULL UNIQUE,
    ativo BOOLEAN     NOT NULL DEFAULT TRUE
);

-- Ajustes da instalação, em chave/valor (regra 13.2). Chave/valor porque o
-- conjunto cresce com o uso e cada ajuste novo não merece uma migração. O que
-- tem integridade referencial (a OM da casa) mora em organizacao_militar.
CREATE TABLE configuracao (
    chave  VARCHAR(60)  PRIMARY KEY,
    valor  VARCHAR(500) NOT NULL
);


-- =============================================================================
-- MILITAR
-- =============================================================================
-- Núcleo obrigatório = nome + posto/graduação + OM. Identidade, CPF e as datas
-- de antiguidade são OPCIONAIS: o efetivo entrou pela planilha do brigada só com
-- o núcleo e é completado depois pela ficha individual (importador de PDF).
-- UNIQUE com NULL permitido: no PostgreSQL vários NULL convivem no índice único.
CREATE TABLE militar (
    id                 INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,  -- SERIAL
    nome_guerra        VARCHAR(60)  NOT NULL,
    nome_completo      VARCHAR(160) NOT NULL,
    identidade         VARCHAR(20)  UNIQUE,             -- varchar: preserva zero à esquerda
    cpf                VARCHAR(14)  UNIQUE,             -- idem
    posto_graduacao_id INT          NOT NULL REFERENCES posto_graduacao (id),
    om_id              INT          NOT NULL REFERENCES organizacao_militar (id),
    data_promocao      DATE,                            -- regra 9.2
    data_praca         DATE,                            -- regra 9.3
    data_nascimento    DATE,                            -- art. 17 (mais velho = mais antigo)
    numero_antiguidade INT,                             -- regra 9.5 (só praças)
    ordem_manual       INT          NOT NULL DEFAULT 0, -- desempate do brigada (regra 9.4)
    ativo              BOOLEAN      NOT NULL DEFAULT TRUE
);


-- =============================================================================
-- ESCALA e seus POSTOS (vagas)
-- =============================================================================
-- Entidade criada/excluída pelo gestor (seção 4). Não há escalas fixas.
CREATE TABLE escala (
    id                 INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nome               VARCHAR(120) NOT NULL,
    tem_preta          BOOLEAN NOT NULL DEFAULT TRUE,
    tem_vermelha       BOOLEAN NOT NULL DEFAULT TRUE,   -- Museu: só vermelha (regra 4.5)
    folga_minima_horas INT,                             -- regra 7.2 (NULL = default 48h)
    inicio_servico     TIME    NOT NULL DEFAULT '08:00',-- janela do serviço (regra 2.4)
    duracao_horas      INT     NOT NULL DEFAULT 24,     -- regra 2.4
    ativa              BOOLEAN NOT NULL DEFAULT TRUE,    -- ciclo de vida (regra 4.4)
    -- piso rígido de 24h (regra 7.2); o default 48h é aplicado no código quando NULL
    CONSTRAINT ck_folga_piso CHECK (folga_minima_horas IS NULL OR folga_minima_horas >= 24),
    CONSTRAINT ck_duracao    CHECK (duracao_horas > 0),
    -- regra 4.5: sem nenhuma cor a escala nunca escalaria (achado do code-review)
    CONSTRAINT ck_ao_menos_uma_cor CHECK (tem_preta OR tem_vermelha)
);

-- Posto = cada vaga da escala num dia (regra 2.5). O nº de postos da escala é a
-- contagem de linhas aqui. Substitui o antigo `posto_rotulo` embutido em servico.
CREATE TABLE posto (
    id         INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    escala_id  INT NOT NULL REFERENCES escala (id) ON DELETE CASCADE,
    ordem      INT NOT NULL,                    -- 1..N dentro da escala
    rotulo     VARCHAR(80),                     -- ex.: 'Comandante da Guarda' (opcional)
    UNIQUE (escala_id, ordem)
);

-- Concorrência entre escalas: relação explícita e SIMÉTRICA (regra 7.4.1).
-- Uma linha por par, com a<b, para não duplicar a simetria.
CREATE TABLE escala_concorrente (
    escala_menor_id INT NOT NULL REFERENCES escala (id) ON DELETE CASCADE,
    escala_maior_id INT NOT NULL REFERENCES escala (id) ON DELETE CASCADE,
    PRIMARY KEY (escala_menor_id, escala_maior_id),
    CONSTRAINT ck_par_ordenado CHECK (escala_menor_id < escala_maior_id)
);

-- Participação militar <-> escala (regra 3.3). `ativo` = participa hoje;
-- isenção permanente = não-participação (ativo FALSE ou sem linha) — regra 7.6.
-- SEM colunas ultima_preta/ultima_vermelha: as datas de fila derivam de `servico`.
CREATE TABLE participacao (
    id         INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    militar_id INT NOT NULL REFERENCES militar (id) ON DELETE CASCADE,
    escala_id  INT NOT NULL REFERENCES escala (id) ON DELETE CASCADE,
    ativo      BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (militar_id, escala_id)
);


-- =============================================================================
-- CALENDÁRIO
-- =============================================================================
-- Feriados nacionais embutidos (nacional=TRUE) + adicionados pelo gestor (regra 5.2).
CREATE TABLE feriado (
    id       INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    data     DATE NOT NULL UNIQUE,
    nome     VARCHAR(120) NOT NULL,
    nacional BOOLEAN NOT NULL DEFAULT FALSE
);

-- Override: o gestor força a cor de um dia específico, com observação (regra 5.3).
-- Vale nos dois sentidos: dia útil declarado vermelha (o caso da regra escrita) e
-- feriado/fim de semana declarado preta, p/ feriado trabalhado — generalização
-- ainda PENDENTE de validação com o Brigada.
CREATE TABLE override_dia (
    data        DATE PRIMARY KEY,
    cor         cor  NOT NULL DEFAULT 'vermelha',
    observacao  VARCHAR(200)
);


-- =============================================================================
-- IMPEDIMENTOS (dispensa, férias, curso, operação) — regra 7.5
-- =============================================================================
CREATE TABLE impedimento (
    id                  INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    militar_id          INT  NOT NULL REFERENCES militar (id) ON DELETE CASCADE,
    tipo_impedimento_id INT  NOT NULL REFERENCES tipo_impedimento (id),
    inicio              DATE NOT NULL,
    fim                 DATE NOT NULL,
    observacao          VARCHAR(200),
    CONSTRAINT ck_periodo CHECK (fim >= inicio)
);


-- =============================================================================
-- SERVIÇO — a unidade militar-dia-posto (quem efetivamente foi escalado)
-- =============================================================================
-- A folga fica colada em `militar_id` (o ESCALADO), nunca no substituto (regra 9).
-- `cor`, `dia` e `termino_dt` são gravados como retrato do momento da escalação:
--   - a fila (mais folgado) deriva daqui (max(dia)/max(termino_dt) por militar/cor);
--   - `termino_dt` alimenta a folga mínima entre escalas concorrentes (regra 7).
CREATE TABLE servico (
    id         INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    escala_id  INT       NOT NULL REFERENCES escala (id),
    posto_id   INT       NOT NULL REFERENCES posto (id),
    militar_id INT       NOT NULL REFERENCES militar (id),  -- o escalado
    dia        DATE      NOT NULL,                          -- dia em que o serviço começa
    cor        cor       NOT NULL,
    inicio_dt  TIMESTAMP NOT NULL,                          -- escala.inicio_em(dia)
    termino_dt TIMESTAMP NOT NULL,                          -- escala.termino_em(dia)
    UNIQUE (posto_id, dia)                                  -- um militar por vaga por dia
);


-- =============================================================================
-- USUÁRIO (gestor) — múltiplos gestores; consulta aberta, gestão exige login (regra 11)
-- =============================================================================
CREATE TABLE usuario (
    id         INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    login      VARCHAR(60)  NOT NULL UNIQUE,
    senha_hash VARCHAR(255) NOT NULL,
    nome       VARCHAR(120) NOT NULL,
    ativo      BOOLEAN NOT NULL DEFAULT TRUE
);


-- =============================================================================
-- PERMUTA (troca) — registro puro, ancorado no serviço (regra 9)
-- =============================================================================
-- Sem retribuição automática nem recálculo de folga. A folga NÃO muda de dono:
-- segue em servico.militar_id. Negar a permuta (no código) se ferir a folga
-- mínima de quem vai cobrir.
CREATE TABLE permuta (
    id                    INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    servico_id            INT       NOT NULL UNIQUE REFERENCES servico (id) ON DELETE CASCADE,
    militar_substituto_id INT       NOT NULL REFERENCES militar (id),
    autorizado_por        INT       REFERENCES usuario (id),
    criado_em             TIMESTAMP NOT NULL DEFAULT now(),
    observacao            VARCHAR(200)
);


-- =============================================================================
-- AUDITORIA (regra 11)
-- =============================================================================
-- Histórico de todas as alterações manuais (regra 11).
-- NOTA: os models usam o tipo JSON genérico do SQLAlchemy (mesmo código roda em
-- SQLite no desenvolvimento), então no PostgreSQL as colunas saem como `json`,
-- não `jsonb` como esta revisão previa. Só são lidas inteiras, para exibir o
-- antes/depois — nunca consultadas por dentro, que é onde jsonb valeria.
CREATE TABLE auditoria (
    id           INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    usuario_id   INT       REFERENCES usuario (id),
    entidade     VARCHAR(60) NOT NULL,     -- ex.: 'servico', 'escala'
    entidade_id  INT,                      -- NULL quando a PK não é um int (override_dia)
    acao         VARCHAR(20) NOT NULL,     -- 'criar' | 'alterar' | 'excluir' | 'escalar'
    dados_antes  JSON,
    dados_depois JSON,
    criado_em    TIMESTAMP NOT NULL DEFAULT now()
);


-- =============================================================================
-- ÍNDICES úteis para o motor
-- =============================================================================
CREATE INDEX ix_servico_militar_cor    ON servico (militar_id, cor, dia);
CREATE INDEX ix_servico_escala_dia     ON servico (escala_id, dia);
CREATE INDEX ix_servico_termino        ON servico (militar_id, termino_dt);
CREATE INDEX ix_participacao_escala    ON participacao (escala_id) WHERE ativo;
CREATE INDEX ix_impedimento_militar    ON impedimento (militar_id, inicio, fim);


-- =============================================================================
-- SEED — dados fixos por lei (art. 16). Revisar os valores.
-- =============================================================================
INSERT INTO circulo_hierarquico (nome, ordem, eh_praca) VALUES
    ('Oficiais-Generais',     6, FALSE),
    ('Oficiais Superiores',   5, FALSE),
    ('Oficiais Intermediários',4, FALSE),
    ('Oficiais Subalternos',  3, FALSE),
    ('Subtenentes e Sargentos',2, TRUE),
    ('Cabos e Soldados',      1, TRUE);

-- ordem_hierarquica reaproveita os índices do domínio (POSTO_ORDEM, art. 16).
INSERT INTO posto_graduacao (sigla, nome, ordem_hierarquica, circulo_id) VALUES
    ('Gen Ex', 'General de Exército',        32, (SELECT id FROM circulo_hierarquico WHERE nome='Oficiais-Generais')),
    ('Gen Div','General de Divisão',         31, (SELECT id FROM circulo_hierarquico WHERE nome='Oficiais-Generais')),
    ('Gen Bda','General de Brigada',         30, (SELECT id FROM circulo_hierarquico WHERE nome='Oficiais-Generais')),
    ('Cel',    'Coronel',                    26, (SELECT id FROM circulo_hierarquico WHERE nome='Oficiais Superiores')),
    ('Ten Cel','Tenente-Coronel',            25, (SELECT id FROM circulo_hierarquico WHERE nome='Oficiais Superiores')),
    ('Maj',    'Major',                      24, (SELECT id FROM circulo_hierarquico WHERE nome='Oficiais Superiores')),
    ('Cap',    'Capitão',                    23, (SELECT id FROM circulo_hierarquico WHERE nome='Oficiais Intermediários')),
    ('1º Ten', 'Primeiro-Tenente',           22, (SELECT id FROM circulo_hierarquico WHERE nome='Oficiais Subalternos')),
    ('2º Ten', 'Segundo-Tenente',            21, (SELECT id FROM circulo_hierarquico WHERE nome='Oficiais Subalternos')),
    -- Praças especiais (art. 19): alocadas no círculo de oficiais subalternos por decisão do gestor.
    ('Asp Of', 'Aspirante a Oficial',        20, (SELECT id FROM circulo_hierarquico WHERE nome='Oficiais Subalternos')),
    ('Cad',    'Cadete',                     19, (SELECT id FROM circulo_hierarquico WHERE nome='Oficiais Subalternos')),
    ('S Ten',  'Subtenente',                 14, (SELECT id FROM circulo_hierarquico WHERE nome='Subtenentes e Sargentos')),
    ('1º Sgt', 'Primeiro-Sargento',          13, (SELECT id FROM circulo_hierarquico WHERE nome='Subtenentes e Sargentos')),
    ('2º Sgt', 'Segundo-Sargento',           12, (SELECT id FROM circulo_hierarquico WHERE nome='Subtenentes e Sargentos')),
    ('3º Sgt', 'Terceiro-Sargento',          11, (SELECT id FROM circulo_hierarquico WHERE nome='Subtenentes e Sargentos')),
    ('Cb',     'Cabo',                        9, (SELECT id FROM circulo_hierarquico WHERE nome='Cabos e Soldados')),
    ('Sd',     'Soldado',                     8, (SELECT id FROM circulo_hierarquico WHERE nome='Cabos e Soldados'));
