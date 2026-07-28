"""Backup e restauração da instalação (regra 13.3 — deploy local, uma OM).

O sistema roda **na OM**, num container que a TI local sobe (regra 13.3). Não
há operação central para restaurar nada: se o servidor se perder, quem tem o
arquivo é a própria OM. Por isso baixar e restaurar tem de caber numa tela, e
não numa instrução de terminal que ninguém vai lembrar.

**Backup do banco É backup das configurações.** Depois que as telas de
Configuração passaram a guardar tudo no banco — OM da casa, OMs de origem,
postos/graduações com a ordem hierárquica (que decide o desempate da fila),
tipos de impedimento, gestores, feriados, contato do suporte —, o `.env` ficou
só com `DATABASE_URL`, `TZ` e a sigla de reserva do primeiro boot. Nada disso
precisa de backup: é o que a TI digita ao subir o container.

Fica **de fora, de propósito**, o `dados/secret_key`: é segredo, não é dado, e
perdê-lo custa uma sessão derrubada — não um serviço a menos na escala. Guardá-lo
junto do banco espalharia a chave de assinatura por todo pen drive de backup.

Decisões que sustentam este módulo:

  - **A cópia usa a API de backup do SQLite** (`sqlite3.Connection.backup`), não
    `shutil.copy`. Com WAL ligado (`app/database.py`) o que foi gravado há
    segundos ainda pode estar no arquivo `-wal`: copiar só o `.sqlite3` pode
    render um arquivo sem os últimos serviços gravados — ou inconsistente, se
    alguém escrever durante a cópia. A API tira um retrato consistente com o
    sistema no ar, que é o caso (a consulta é aberta, regra 13.1).
  - **Restaurar confere antes** (duas etapas, como a ficha em PDF e o CSV): a
    tela mostra de que OM é o arquivo, quantos militares e serviços tem, até
    quando vai e quais gestores existem lá dentro. Restaurar às cegas um arquivo
    de outra instalação é o erro caro, e ele acontece com o nome do arquivo
    parecendo certo.
  - **Nada é sobrescrito.** O banco atual vira `...-antes-da-restauracao-<ts>`
    ao lado do novo. Se a migração pós-restauração falhar, ele volta ao lugar.
  - **Backup mais NOVO que o código é recusado**: o Alembic sobe de versão, não
    desce. Mais antigo é aceito e migrado.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app import VERSAO
from app.config import settings

# Todo arquivo SQLite começa com estes 16 bytes. Checar o cabeçalho antes de
# abrir evita tratar um .ods, um PDF ou um .sqlite3 truncado como banco.
MAGIC = b"SQLite format 3\x00"

# Teto do arquivo aceito na restauração. O banco real de uma OM com 285
# militares e um ano de serviços tem 320 KB; 200 MB cobre décadas com folga e
# ainda barra o upload por engano de um arquivo que não tem nada a ver.
TAMANHO_MAXIMO = 200 * 1024 * 1024

# Onde o arquivo enviado espera entre "conferir" e "confirmar". Fica ao lado do
# banco de propósito: mesmo sistema de arquivos, então pôr o novo no lugar é um
# rename atômico, e dentro do volume do container.
PASTA_ENVIOS = "restauracao"
VALIDADE_ENVIO = timedelta(hours=2)

# Backups automáticos: um por dia, ao lado do banco, guardando a última semana.
# Existem porque o cenário que decide a compra do sistema não é "quero uma
# cópia", é **"esta máquina está com problema, preciso subir outra com o estado
# de ontem"** — e ontem só existe se alguém tiver gerado sem depender de
# lembrança. Com o banco real (320 KB), sete cópias ocupam ~2,5 MB.
#
# Eles NÃO substituem baixar: moram no mesmo disco. Salvam do erro humano
# (apagou o mês errado) e da troca de máquina com o disco íntegro, que é o caso
# comum. Disco perdido só o arquivo que saiu daqui resolve.
PASTA_AUTOMATICOS = "backups"
MANTER_AUTOMATICOS = 7
# De quanto em quanto tempo o laço de fundo PERGUNTA se falta o backup do dia.
# Uma hora, e não 24: dormindo um dia inteiro a partir do boot, um container que
# subiu às 23h50 só geraria o backup do dia seguinte às 23h50 — quase 24h de
# atraso. Quem decide se há o que fazer é a data do arquivo, não o relógio do laço.
INTERVALO_CHECAGEM = timedelta(hours=1)

RAIZ = Path(__file__).resolve().parents[2]      # .../escala


class ErroBackup(Exception):
    """Recusa com motivo legível para o gestor (não é erro de sistema)."""


# --- onde está o banco --------------------------------------------------------
def caminho_do_banco(url: str | None = None) -> Path | None:
    """Arquivo do banco desta instalação, ou None se não houver arquivo.

    None em dois casos legítimos: `DATABASE_URL` apontando para PostgreSQL (o
    backup ali é `pg_dump`, e oferecer um botão que produz lixo seria pior que
    não oferecer) e o `:memory:` dos testes.
    """
    alvo = make_url(url or settings.database_url)
    if not alvo.drivername.startswith("sqlite"):
        return None
    if not alvo.database or alvo.database == ":memory:":
        return None
    return Path(alvo.database).resolve()


def eh_arquivo(url: str | None = None) -> bool:
    """Esta instalação tem banco em arquivo? (só aí baixar/restaurar faz sentido)"""
    return caminho_do_banco(url) is not None


def nome_sugerido(sigla: str, agora: datetime | None = None) -> str:
    """`escala-1BI-2026-07-28-1432.sqlite3` — a OM e o instante no nome.

    O nome é a única coisa que diz de quando é o arquivo depois que ele sai
    daqui: data de modificação se perde em cópia, anexo de e-mail e pen drive.
    """
    agora = agora or datetime.now()
    return f"escala-{sigla_no_nome(sigla)}-{agora:%Y-%m-%d-%H%M}.sqlite3"


def sigla_no_nome(sigla: str) -> str:
    """Só ASCII alfanumérico: `1º BI` vira `1BI`.

    `isalnum()` sozinho deixaria passar o `º` — e a sigla militar quase sempre
    tem um. O `Content-Disposition` é cabeçalho HTTP: caractere fora do ASCII
    ali sai truncado ou escapado, e o gestor recebe um arquivo de nome
    estranho justamente no momento em que precisa reconhecê-lo depois.
    """
    limpa = "".join(c for c in (sigla or "") if c.isascii() and c.isalnum())
    return limpa or "OM"


# --- baixar -------------------------------------------------------------------
def _conexao_crua(db: Session) -> sqlite3.Connection:
    bruta = db.connection().connection
    crua = getattr(bruta, "dbapi_connection", bruta)
    if not isinstance(crua, sqlite3.Connection):
        raise ErroBackup(
            "Esta instalação não usa SQLite. O backup do PostgreSQL é feito com "
            "pg_dump, pela TI que administra o servidor do banco.")
    return crua


def copia(db: Session) -> bytes:
    """Retrato consistente do banco inteiro, pronto para ser baixado.

    Usa a conexão da PRÓPRIA sessão do pedido: assim o backup é sempre do banco
    a que a aplicação está de fato ligada, sem reabrir o arquivo por um caminho
    que poderia estar desatualizado.
    """
    db.rollback()                       # nada pendente: o retrato é do que está gravado
    origem = _conexao_crua(db)
    with tempfile.TemporaryDirectory() as tmp:
        destino = Path(tmp) / "backup.sqlite3"
        alvo = sqlite3.connect(destino)
        try:
            origem.backup(alvo)         # API de backup do SQLite, com o app no ar
        finally:
            alvo.close()
        return destino.read_bytes()


# --- o que há dentro de um arquivo enviado ------------------------------------
@dataclass(frozen=True)
class Gestor:
    login: str
    nome: str
    ativo: bool


@dataclass(frozen=True)
class ArquivoBackup:
    """Um backup automático em disco, do jeito que a tela o lista."""
    nome: str
    dia: date | None
    tamanho: int
    caminho: Path


@dataclass(frozen=True)
class Retrato:
    """O que o arquivo enviado contém — mostrado ANTES de qualquer troca.

    `ultima_alteracao` é o carimbo mais recente da auditoria, e não a data do
    backup: um arquivo SQLite não guarda quando foi copiado. É a melhor resposta
    honesta para "de quando é isto?" — e é a que interessa, porque diz até onde
    o trabalho registrado chega.
    """
    revisao: str
    revisao_atual: str
    versao: str                     # versão da aplicação que gerou o backup
    versao_atual: str
    om: str
    militares: int
    escalas: int
    servicos: int
    impedimentos: int
    ultimo_servico: date | None
    ultima_alteracao: datetime | None
    gestores: list[Gestor] = field(default_factory=list)
    tamanho: int = 0

    @property
    def precisa_migrar(self) -> bool:
        return self.revisao != self.revisao_atual

    def tem_gestor(self, login: str) -> bool:
        alvo = (login or "").strip().lower()
        return any(g.login.lower() == alvo and g.ativo for g in self.gestores)


def _revisoes() -> tuple[str, set[str]]:
    """(revisão que o código espera, todas as revisões que ele conhece)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    return script.get_current_head() or "", {r.revision for r in script.walk_revisions()}


def _conta(cur: sqlite3.Cursor, sql: str, padrao=0):
    """Consulta tolerante: tabela ausente num backup antigo não derruba a tela."""
    try:
        linha = cur.execute(sql).fetchone()
    except sqlite3.Error:
        return padrao
    return (linha[0] if linha and linha[0] is not None else padrao)


def _data(bruto) -> date | None:
    try:
        return date.fromisoformat(str(bruto)[:10]) if bruto else None
    except ValueError:
        return None


def _datahora(bruto) -> datetime | None:
    try:
        return datetime.fromisoformat(str(bruto)) if bruto else None
    except ValueError:
        return None


def inspecionar(arquivo: Path) -> Retrato:
    """Abre o arquivo e diz o que ele é. Recusa com motivo o que não serve."""
    if not arquivo.is_file():
        raise ErroBackup("Arquivo não encontrado.")
    tamanho = arquivo.stat().st_size
    if tamanho == 0:
        raise ErroBackup("Arquivo vazio.")
    if tamanho > TAMANHO_MAXIMO:
        raise ErroBackup(
            f"Arquivo grande demais (limite de {TAMANHO_MAXIMO // (1024 * 1024)} MB).")
    with arquivo.open("rb") as f:
        if f.read(16) != MAGIC:
            raise ErroBackup(
                "Este arquivo não é um banco SQLite. O backup do sistema termina "
                "em .sqlite3 e é o que a própria tela gera — planilha, PDF ou ZIP "
                "de exportação não servem para restaurar.")

    con = sqlite3.connect(arquivo)
    try:
        cur = con.cursor()
        problema = cur.execute("PRAGMA quick_check").fetchone()
        if not problema or problema[0] != "ok":
            raise ErroBackup(
                "O arquivo está corrompido (a verificação do SQLite não passou). "
                "Use outra cópia — restaurar este banco levaria o defeito junto.")

        revisao = _conta(cur, "SELECT version_num FROM alembic_version LIMIT 1", "")
        atual, conhecidas = _revisoes()
        if not revisao:
            raise ErroBackup(
                "O arquivo não tem a marca de versão do banco (tabela "
                "alembic_version). Não é um backup deste sistema.")
        if revisao not in conhecidas:
            raise ErroBackup(
                f"O backup foi feito por uma versão MAIS NOVA do sistema (banco "
                f"'{revisao}', desconhecido aqui). Atualize a aplicação antes de "
                f"restaurar: o banco sobe de versão, nunca desce.")

        om = _conta(cur, "SELECT sigla FROM organizacao_militar WHERE propria = 1 "
                         "LIMIT 1", "") or "(OM não definida)"
        gestores = []
        try:
            for login, nome, ativo in cur.execute(
                    "SELECT login, nome, ativo FROM usuario ORDER BY login"):
                gestores.append(Gestor(login=login, nome=nome, ativo=bool(ativo)))
        except sqlite3.Error:
            pass

        return Retrato(
            revisao=revisao,
            revisao_atual=atual,
            # A revisão do Alembic é a checagem CERTA, mas ilegível para quem
            # está decidindo às 22h se aquele arquivo serve. A versão da
            # aplicação é a mesma que o rodapé das telas mostra.
            versao=_conta(cur, "SELECT valor FROM configuracao WHERE chave = "
                               "'versao_aplicacao'", "") or "",
            versao_atual=VERSAO,
            om=om,
            militares=_conta(cur, "SELECT COUNT(*) FROM militar WHERE ativo = 1"),
            escalas=_conta(cur, "SELECT COUNT(*) FROM escala WHERE ativa = 1"),
            servicos=_conta(cur, "SELECT COUNT(*) FROM servico"),
            impedimentos=_conta(cur, "SELECT COUNT(*) FROM impedimento"),
            ultimo_servico=_data(_conta(cur, "SELECT MAX(dia) FROM servico", None)),
            ultima_alteracao=_datahora(
                _conta(cur, "SELECT MAX(criado_em) FROM auditoria", None)),
            gestores=gestores,
            tamanho=tamanho,
        )
    finally:
        con.close()


# --- backups automáticos ------------------------------------------------------
def pasta_automaticos() -> Path | None:
    banco = caminho_do_banco()
    if banco is None:
        return None
    pasta = banco.parent / PASTA_AUTOMATICOS
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def carimbar_versao(con: sqlite3.Connection) -> None:
    """Grava no banco a versão da aplicação, para que o BACKUP a carregue.

    Sem isto, o único jeito de saber se um arquivo serve seria a revisão do
    Alembic — que é a checagem correta, mas ilegível justamente para quem está
    escolhendo entre dois arquivos no meio de uma troca de máquina.

    Falha em silêncio: banco recém-criado, antes das migrações, não tem a tabela
    `configuracao`, e um backup sem carimbo é melhor que backup nenhum.
    """
    try:
        con.execute(
            "INSERT INTO configuracao (chave, valor) VALUES ('versao_aplicacao', ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor", (VERSAO,))
        con.commit()
    except sqlite3.Error:
        pass


def nome_automatico(dia: date) -> str:
    """`escala-2026-07-28.sqlite3` — um por dia, e a data ordena sozinha."""
    return f"escala-{dia:%Y-%m-%d}.sqlite3"


def _dia_do_nome(nome: str) -> date | None:
    try:
        return date.fromisoformat(nome[len("escala-"):-len(".sqlite3")])
    except (ValueError, IndexError):
        return None


def automaticos() -> list[ArquivoBackup]:
    """Os backups automáticos que existem, do mais recente para trás."""
    pasta = pasta_automaticos()
    if pasta is None:
        return []
    achados = []
    for arq in pasta.glob("escala-*.sqlite3"):
        dia = _dia_do_nome(arq.name)
        if dia is None:
            continue
        try:
            achados.append(ArquivoBackup(arq.name, dia, arq.stat().st_size, arq))
        except OSError:
            continue
    return sorted(achados, key=lambda a: a.dia, reverse=True)


def arquivo_automatico(nome: str) -> Path:
    """Resolve o nome vindo da URL. Só o que ESTE módulo gera passa.

    A validação é por FORMATO, não por sanitização de caminho: o nome tem de
    ser exatamente `escala-<data ISO>.sqlite3`. Assim `..%2F` e afins não têm
    por onde entrar — não existe nome com barra que case com o padrão.
    """
    pasta = pasta_automaticos()
    dia = _dia_do_nome(nome or "")
    if pasta is None or dia is None or nome != nome_automatico(dia):
        raise ErroBackup("Backup automático não encontrado.")
    alvo = pasta / nome
    if not alvo.is_file():
        raise ErroBackup("Backup automático não encontrado.")
    return alvo


def podar_automaticos(manter: int = MANTER_AUTOMATICOS) -> int:
    """Apaga os mais antigos além do limite. Devolve quantos saíram."""
    sobrando = automaticos()[manter:]
    for velho in sobrando:
        try:
            velho.caminho.unlink()
        except OSError:
            continue
    return len(sobrando)


def gerar_automatico(hoje: date | None = None, forcar: bool = False) -> Path | None:
    """Grava o backup do dia, se ainda não houver. Devolve o arquivo, ou None.

    Abre a própria conexão ao arquivo do banco — roda no boot e num laço de
    fundo, onde não existe sessão de pedido. Continua sendo a API de backup do
    SQLite: o gestor pode estar escalando o mês no exato instante.

    Um por dia e não um por execução: o container reinicia (`restart:
    unless-stopped`) e a pasta encheria de cópias do mesmo estado, empurrando
    para fora justamente os dias anteriores — que são o que se quer guardar.
    """
    banco = caminho_do_banco()
    pasta = pasta_automaticos()
    if banco is None or pasta is None or not banco.is_file():
        return None
    hoje = hoje or date.today()
    destino = pasta / nome_automatico(hoje)
    if destino.exists() and not forcar:
        return None

    parcial = destino.with_suffix(".parcial")
    origem = sqlite3.connect(banco)
    try:
        carimbar_versao(origem)
        alvo = sqlite3.connect(parcial)
        try:
            origem.backup(alvo)
        finally:
            alvo.close()
    finally:
        origem.close()
    # Só vira backup do dia depois de completo: um `.sqlite3` truncado por queda
    # de energia no meio da cópia seria pior que backup nenhum — pareceria bom.
    os.replace(parcial, destino)
    podar_automaticos()
    return destino


# --- o arquivo enviado, entre conferir e confirmar ----------------------------
def _pasta_envios() -> Path:
    banco = caminho_do_banco()
    if banco is None:
        raise ErroBackup(
            "Esta instalação não guarda o banco em arquivo — não há o que restaurar "
            "por aqui.")
    pasta = banco.parent / PASTA_ENVIOS
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def caminho_envio(token: str) -> Path:
    """Onde mora o arquivo já enviado. O token é gerado aqui, nunca vem da URL
    sem passar por esta peneira — `..` e barra viram arquivo inexistente."""
    limpo = "".join(c for c in (token or "") if c.isalnum() or c in "-_")
    if not limpo:
        raise ErroBackup("Envio não identificado. Escolha o arquivo de novo.")
    return _pasta_envios() / f"{limpo}.sqlite3"


def limpar_envios(agora: datetime | None = None) -> int:
    """Apaga envios que ninguém confirmou. Um banco inteiro largado no disco por
    quem desistiu no meio é lixo — e lixo com os dados pessoais do efetivo."""
    agora = agora or datetime.now()
    try:
        pasta = _pasta_envios()
    except ErroBackup:
        return 0
    apagados = 0
    for velho in pasta.glob("*.sqlite3"):
        try:
            nascido = datetime.fromtimestamp(velho.stat().st_mtime)
            if agora - nascido > VALIDADE_ENVIO:
                velho.unlink()
                apagados += 1
        except OSError:
            continue
    return apagados


def guardar_envio(conteudo: bytes) -> str:
    """Grava o arquivo enviado e devolve o token que a etapa 2 vai usar.

    O conteúdo NÃO viaja no formulário como acontece no CSV: um banco de
    centenas de KB em campo oculto viraria um megabyte de base64 a cada volta da
    página. O que garante que se confirma o que se conferiu é a **releitura**:
    a etapa 2 inspeciona o arquivo de novo antes de trocar qualquer coisa.
    """
    import secrets

    if len(conteudo) > TAMANHO_MAXIMO:
        raise ErroBackup(
            f"Arquivo grande demais (limite de {TAMANHO_MAXIMO // (1024 * 1024)} MB).")
    if not conteudo:
        raise ErroBackup("Arquivo vazio.")
    limpar_envios()
    token = secrets.token_urlsafe(16)
    destino = caminho_envio(token)
    destino.write_bytes(conteudo)
    return token


# --- restaurar ----------------------------------------------------------------
@dataclass(frozen=True)
class Restauracao:
    retrato: Retrato
    copia_de_seguranca: Path | None


def _migrar_para_head() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "alembic"))
    command.upgrade(cfg, "head")


def restaurar(token: str) -> Restauracao:
    """Põe o arquivo enviado no lugar do banco atual. Devolve onde o antigo ficou.

    A ordem importa e cada passo tem motivo:

      1. **inspecionar de novo** — entre conferir e confirmar o arquivo é o
         mesmo, mas a checagem custa milissegundos e é o que impede confirmar
         um envio adulterado ou truncado no meio;
      2. **soltar as conexões** (`engine.dispose`) — no Windows não se renomeia
         arquivo aberto, e mesmo no Linux trocar o arquivo debaixo de uma
         conexão viva é receita de leitura fantasma;
      3. **guardar o atual** com nome datado, em vez de sobrescrever;
      4. **apagar `-wal`/`-shm`** — eles pertencem ao banco ANTIGO. Deixados no
         lugar, o SQLite os associaria ao arquivo novo e o corromperia;
      5. **migrar** se o backup for de versão anterior; falhando, o banco
         anterior volta ao lugar — restauração que quebra a instalação é pior
         que restauração que não acontece.
    """
    from app.database import engine

    destino = caminho_do_banco()
    if destino is None:
        raise ErroBackup(
            "Esta instalação não guarda o banco em arquivo — não há o que restaurar "
            "por aqui.")
    origem = caminho_envio(token)
    if not origem.is_file():
        raise ErroBackup(
            "O arquivo enviado não está mais disponível (a conferência expira em "
            f"{int(VALIDADE_ENVIO.total_seconds() // 3600)}h). Envie-o de novo.")

    retrato = inspecionar(origem)

    engine.dispose()
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")
    copia_seg: Path | None = None
    if destino.exists():
        copia_seg = destino.with_name(f"{destino.stem}-antes-da-restauracao-{marca}"
                                      f"{destino.suffix}")
        shutil.copy2(destino, copia_seg)

    for sufixo in ("-wal", "-shm"):
        Path(str(destino) + sufixo).unlink(missing_ok=True)
    os.replace(origem, destino)

    if retrato.precisa_migrar:
        try:
            _migrar_para_head()
        except Exception as e:                              # noqa: BLE001
            engine.dispose()
            if copia_seg is not None:
                for sufixo in ("-wal", "-shm"):
                    Path(str(destino) + sufixo).unlink(missing_ok=True)
                shutil.copy2(copia_seg, destino)
            raise ErroBackup(
                "O backup é de uma versão anterior e a atualização do banco falhou; "
                f"o banco que estava em uso foi recolocado. Detalhe: {e}") from e

    return Restauracao(retrato=retrato, copia_de_seguranca=copia_seg)
