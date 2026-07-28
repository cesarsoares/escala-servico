"""Exportação dos dados em CSV (pacote ZIP) — coisa DIFERENTE de backup.

O backup (`services/backup.py`) existe para **restaurar**: é o banco inteiro,
binário, e só serve dentro deste sistema. A exportação existe para **ler**:
abrir no Excel, conferir uma contagem, mandar o histórico para a seção de
pessoal, prestar informação a quem pediu, ou levar o passado para outra
instalação sem carregar junto gestores, senhas e auditoria.

Confundir os dois é o erro que custa caro no dia ruim — por isso o ZIP leva um
`LEIA-ME.txt` dizendo, na primeira linha, que ele **não** restaura nada.

Decisões:

  - **`servicos.csv` sai no formato que `/gestao/importar` consome**
    (`services/importacao_csv.COLUNAS`). É o que fecha o par exportar/importar:
    o arquivo que sai daqui volta por lá, numa instalação nova, sem ninguém
    reescrever cabeçalho. O posto sai pelo rótulo, ou pelo número da ordem
    quando não tem rótulo — as duas formas o importador entende.
  - **Excel em português**: separador `;` e `utf-8-sig` (o BOM é o que faz o
    acento abrir certo com dois cliques), datas em `dd/mm/aaaa`, booleanos em
    `sim`/`não`. O mesmo dialeto que o importador já fala.
  - **CPF e identidade são opcionais e saem desmarcados.** O ZIP vai para pen
    drive e anexo de e-mail; dado pessoal do efetivo não deve viajar por
    descuido. Quem precisa marca a caixa e assume.
  - Vale também em PostgreSQL: aqui é tudo SQL, nada depende do arquivo.
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.models.calendario import Feriado, OverrideDia
from app.models.escala import Escala, EscalaConcorrente, Participacao, Posto
from app.models.gestao import Auditoria, Usuario
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao, TipoImpedimento
from app.models.servico import Permuta, Servico
from app.services.importacao_csv import COLUNAS as COLUNAS_SERVICO

SEPARADOR = ";"


def _sn(valor) -> str:
    return "sim" if valor else "não"


def _d(valor: date | None) -> str:
    return valor.strftime("%d/%m/%Y") if valor else ""


def _hora_local(dt: datetime | None) -> str:
    """Mesma regra do filtro `web.hora_local`: o banco grava em UTC.

    `auditoria.criado_em` vem de `func.now()`, que no SQLite é UTC e sem tzinfo.
    Exportar o valor cru adiantaria todo horário em 3h — num registro de "quem
    mexeu e quando" isso é defeito, e num arquivo que sai da casa é pior, porque
    ninguém tem como desconfiar depois.
    """
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%d/%m/%Y %H:%M")


def _csv(cabecalho: list[str], linhas) -> str:
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=SEPARADOR, lineterminator="\r\n",
                          quoting=csv.QUOTE_MINIMAL)
    escritor.writerow(cabecalho)
    escritor.writerows(linhas)
    return saida.getvalue()


# --- cada arquivo -------------------------------------------------------------
def militares(db: Session, incluir_pessoais: bool = False) -> str:
    """O efetivo, na ordem hierárquica — a mesma que ordena a fila (regra 9.1)."""
    cabecalho = ["posto_graduacao", "nome_guerra", "nome_completo", "om",
                 "data_promocao", "data_praca", "data_nascimento",
                 "numero_antiguidade", "ordem_manual", "ativo"]
    if incluir_pessoais:
        cabecalho[4:4] = ["identidade", "cpf"]

    consulta = (
        select(Militar, PostoGraduacao, OrganizacaoMilitar)
        .join(PostoGraduacao, Militar.posto_graduacao_id == PostoGraduacao.id)
        .join(OrganizacaoMilitar, Militar.om_id == OrganizacaoMilitar.id)
        .order_by(PostoGraduacao.ordem_hierarquica.desc(), Militar.nome_guerra)
    )
    linhas = []
    for m, pg, om in db.execute(consulta):
        linha = [pg.sigla, m.nome_guerra, m.nome_completo, om.sigla]
        if incluir_pessoais:
            linha += [m.identidade or "", m.cpf or ""]
        linha += [_d(m.data_promocao), _d(m.data_praca), _d(m.data_nascimento),
                  m.numero_antiguidade if m.numero_antiguidade is not None else "",
                  m.ordem_manual, _sn(m.ativo)]
        linhas.append(linha)
    return _csv(cabecalho, linhas)


def escalas(db: Session) -> str:
    """Como cada escala está configurada — inclusive as extintas (regra 4.4)."""
    postos_por_escala = dict(db.execute(
        select(Posto.escala_id, func.count()).group_by(Posto.escala_id)).all())
    ativos_por_escala = dict(db.execute(
        select(Participacao.escala_id, func.count())
        .where(Participacao.ativo.is_(True))
        .group_by(Participacao.escala_id)).all())
    nomes = dict(db.execute(select(Escala.id, Escala.nome)).all())

    # Concorrência é simétrica (regra 7.4.1): a tabela guarda um par ordenado,
    # e a exportação mostra os dois lados — quem lê a linha da escala A precisa
    # ver B ali, não descobrir numa outra linha.
    vizinhas: dict[int, list[str]] = {}
    for menor, maior in db.execute(
            select(EscalaConcorrente.escala_menor_id, EscalaConcorrente.escala_maior_id)):
        vizinhas.setdefault(menor, []).append(nomes.get(maior, str(maior)))
        vizinhas.setdefault(maior, []).append(nomes.get(menor, str(menor)))

    linhas = []
    for e in db.scalars(select(Escala).order_by(Escala.nome)):
        linhas.append([
            e.nome, _sn(e.tem_preta), _sn(e.tem_vermelha),
            e.folga_minima_horas if e.folga_minima_horas is not None else "",
            e.inicio_servico.strftime("%H:%M"), e.duracao_horas,
            postos_por_escala.get(e.id, 0), ativos_por_escala.get(e.id, 0),
            ", ".join(sorted(vizinhas.get(e.id, []))), _sn(e.ativa),
        ])
    return _csv(["escala", "roda_preta", "roda_vermelha", "folga_minima_horas",
                 "inicio_servico", "duracao_horas", "postos", "participantes_ativos",
                 "escalas_concorrentes", "ativa"], linhas)


def postos(db: Session) -> str:
    """As vagas de cada escala (regra 2.5) — é o que a coluna `posto` do
    histórico referencia."""
    consulta = (
        select(Escala.nome, Posto.ordem, Posto.rotulo)
        .join(Posto, Posto.escala_id == Escala.id)
        .order_by(Escala.nome, Posto.ordem)
    )
    return _csv(["escala", "ordem", "rotulo"],
                [[nome, ordem, rotulo or ""] for nome, ordem, rotulo in db.execute(consulta)])


def participantes(db: Session) -> str:
    """Quem concorre em cada escala e em que cores (regras 3.3 e 3.3.1)."""
    consulta = (
        select(Escala.nome, Militar.nome_guerra, OrganizacaoMilitar.sigla,
               PostoGraduacao.sigla, Participacao.serve_preta,
               Participacao.serve_vermelha, Participacao.ativo)
        .join(Militar, Participacao.militar_id == Militar.id)
        .join(Escala, Participacao.escala_id == Escala.id)
        .join(OrganizacaoMilitar, Militar.om_id == OrganizacaoMilitar.id)
        .join(PostoGraduacao, Militar.posto_graduacao_id == PostoGraduacao.id)
        .order_by(Escala.nome, PostoGraduacao.ordem_hierarquica.desc(), Militar.nome_guerra)
    )
    linhas = [[escala, nome, om, pg, _sn(preta), _sn(vermelha), _sn(ativo)]
              for escala, nome, om, pg, preta, vermelha, ativo in db.execute(consulta)]
    return _csv(["escala", "militar", "om", "posto_graduacao", "serve_preta",
                 "serve_vermelha", "ativo"], linhas)


def servicos(db: Session) -> str:
    """O histórico, no MESMO formato que `/gestao/importar` lê.

    Sem colunas a mais de propósito: acrescentar `cor` aqui convidaria alguém a
    editá-la na planilha, e a cor é consequência da data (regra 5) — quem a
    decide é o calendário da OM, na importação.
    """
    consulta = (
        select(Escala.nome, Servico.dia, Militar.nome_guerra, Posto.rotulo,
               Posto.ordem, OrganizacaoMilitar.sigla)
        .join(Escala, Servico.escala_id == Escala.id)
        .join(Posto, Servico.posto_id == Posto.id)
        .join(Militar, Servico.militar_id == Militar.id)
        .join(OrganizacaoMilitar, Militar.om_id == OrganizacaoMilitar.id)
        .order_by(Servico.dia, Escala.nome, Posto.ordem)
    )
    linhas = [[escala, _d(dia), nome, rotulo or ordem, om]
              for escala, dia, nome, rotulo, ordem, om in db.execute(consulta)]
    return _csv(list(COLUNAS_SERVICO), linhas)


def impedimentos(db: Session) -> str:
    consulta = (
        select(Militar.nome_guerra, OrganizacaoMilitar.sigla, TipoImpedimento.nome,
               Impedimento.inicio, Impedimento.fim, Impedimento.observacao)
        .join(Militar, Impedimento.militar_id == Militar.id)
        .join(OrganizacaoMilitar, Militar.om_id == OrganizacaoMilitar.id)
        .join(TipoImpedimento, Impedimento.tipo_impedimento_id == TipoImpedimento.id)
        .order_by(Impedimento.inicio.desc(), Militar.nome_guerra)
    )
    linhas = [[nome, om, tipo, _d(inicio), _d(fim), obs or ""]
              for nome, om, tipo, inicio, fim, obs in db.execute(consulta)]
    return _csv(["militar", "om", "tipo", "inicio", "fim", "observacao"], linhas)


def permutas(db: Session) -> str:
    """Trocas registradas (regra 9).

    O escalado sai na frente do substituto porque **a folga continua sendo
    dele** — quem lê a planilha tem de ver isso na ordem das colunas, não numa
    nota de rodapé.
    """
    substituto = aliased(Militar)
    escalado = aliased(Militar)
    consulta = (
        select(Escala.nome, Servico.dia, Posto.rotulo, Posto.ordem,
               escalado.nome_guerra, substituto.nome_guerra, Usuario.login,
               Permuta.criado_em, Permuta.observacao)
        .join(Servico, Permuta.servico_id == Servico.id)
        .join(Escala, Servico.escala_id == Escala.id)
        .join(Posto, Servico.posto_id == Posto.id)
        .join(escalado, Servico.militar_id == escalado.id)
        .join(substituto, Permuta.militar_substituto_id == substituto.id)
        .outerjoin(Usuario, Permuta.autorizado_por == Usuario.id)
        .order_by(Servico.dia.desc())
    )
    linhas = [[escala, _d(dia), rotulo or ordem, esc, sub, login or "",
               _hora_local(criado), obs or ""]
              for escala, dia, rotulo, ordem, esc, sub, login, criado, obs
              in db.execute(consulta)]
    return _csv(["escala", "data", "posto", "escalado_folga_e_dele", "substituto",
                 "autorizado_por", "registrado_em", "observacao"], linhas)


def calendario(db: Session) -> str:
    """Feriados (regra 5.2) e dias com cor forçada (5.3) num arquivo só.

    Juntos porque respondem à mesma pergunta — "por que este dia é desta cor?" —
    e separá-los obrigaria a cruzar dois arquivos por data.
    """
    linhas = []
    for f in db.scalars(select(Feriado).order_by(Feriado.data)):
        linhas.append([_d(f.data), "feriado" + (" nacional" if f.nacional else " da OM"),
                       f.nome, ""])
    for o in db.scalars(select(OverrideDia).order_by(OverrideDia.data)):
        linhas.append([_d(o.data), "cor forçada", o.cor.value, o.observacao or ""])
    linhas.sort(key=lambda linha: datetime.strptime(linha[0], "%d/%m/%Y"))
    return _csv(["data", "tipo", "descricao", "observacao"], linhas)


def auditoria(db: Session) -> str:
    """O histórico de quem mexeu em quê (regra 11), do mais recente para trás."""
    consulta = (
        select(Auditoria, Usuario.login)
        .outerjoin(Usuario, Auditoria.usuario_id == Usuario.id)
        .order_by(Auditoria.criado_em.desc(), Auditoria.id.desc())
    )
    linhas = [[_hora_local(a.criado_em), login or "", a.entidade,
               a.entidade_id if a.entidade_id is not None else "", a.acao,
               _texto_json(a.dados_antes), _texto_json(a.dados_depois)]
              for a, login in db.execute(consulta)]
    return _csv(["quando", "gestor", "entidade", "entidade_id", "acao",
                 "antes", "depois"], linhas)


def _texto_json(dados) -> str:
    import json
    return "" if dados is None else json.dumps(dados, ensure_ascii=False, sort_keys=True)


# --- o pacote -----------------------------------------------------------------
LEIA_ME = """EXPORTAÇÃO DE DADOS — Sistema de Escala de Serviço
{om}
Gerada em {quando}

ESTES ARQUIVOS NÃO RESTAURAM O SISTEMA.
Para restaurar uma instalação é preciso o backup do banco (arquivo .sqlite3),
baixado em Configurações > Backup e restauração. Esta exportação serve para
LER os dados fora do sistema: conferir contagens, abrir no Excel, prestar
informação, ou levar o histórico de serviços para outra instalação.

Formato: separador ';' e codificação UTF-8 com BOM — é o que o Excel em
português abre com dois cliques. Datas em dd/mm/aaaa.

O que há em cada arquivo:

  militares.csv       o efetivo, na ordem hierárquica que decide o desempate
                      da fila (regra 9.1).
  escalas.csv         como cada escala está configurada: cores em que roda,
                      folga mínima, janela do serviço, concorrentes.
  postos.csv          as vagas de cada escala (regra 2.5).
  participantes.csv   quem concorre em cada escala e em que cores (regra 3.3.1).
  servicos.csv        o histórico: quem serviu em que dia e em que posto.
                      >> Este arquivo está no MESMO formato que a tela
                         "Importar histórico" lê. É por ele que se leva o
                         passado para uma instalação nova.
  impedimentos.csv    dispensas, férias, cursos e operações (regra 7.5).
  permutas.csv        as trocas registradas (regra 9). Atenção à ordem das
                      colunas: a folga continua sendo do ESCALADO, nunca de
                      quem cobriu.
  calendario.csv      feriados e dias com cor forçada (regras 5.2 e 5.3).
  auditoria.csv       quem alterou o quê e quando (regra 11), no fuso local.

{aviso_pessoais}"""

AVISO_COM_PESSOAIS = (
    "ATENÇÃO: esta exportação inclui CPF e identidade do efetivo, a pedido de\n"
    "quem a gerou. São dados pessoais — trate o arquivo de acordo (não deixe em\n"
    "pasta compartilhada nem em pen drive sem controle).\n")

AVISO_SEM_PESSOAIS = (
    "CPF e identidade NÃO foram incluídos. Para exportar com eles, marque a\n"
    "opção correspondente na tela — e trate o arquivo como dado pessoal.\n")


def arquivos(db: Session, incluir_pessoais: bool = False) -> dict[str, str]:
    """Todos os arquivos do pacote, em texto. Testável sem tocar em ZIP."""
    from app.services.configuracao import identificacao

    ident = identificacao(db)
    return {
        "LEIA-ME.txt": LEIA_ME.format(
            om=f"{ident.sigla} — {ident.nome}",
            quando=datetime.now().strftime("%d/%m/%Y %H:%M"),
            aviso_pessoais=AVISO_COM_PESSOAIS if incluir_pessoais else AVISO_SEM_PESSOAIS),
        "militares.csv": militares(db, incluir_pessoais),
        "escalas.csv": escalas(db),
        "postos.csv": postos(db),
        "participantes.csv": participantes(db),
        "servicos.csv": servicos(db),
        "impedimentos.csv": impedimentos(db),
        "permutas.csv": permutas(db),
        "calendario.csv": calendario(db),
        "auditoria.csv": auditoria(db),
    }


def pacote(db: Session, incluir_pessoais: bool = False) -> bytes:
    """O ZIP pronto para baixar."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for nome, conteudo in arquivos(db, incluir_pessoais).items():
            # BOM só nos CSV: é o que faz o Excel abrir o acento certo. No
            # LEIA-ME ele apareceria como lixo na primeira linha do Bloco de Notas.
            codec = "utf-8-sig" if nome.endswith(".csv") else "utf-8"
            zf.writestr(nome, conteudo.encode(codec))
    return buffer.getvalue()


def nome_do_pacote(sigla: str, agora: datetime | None = None) -> str:
    from app.services.backup import sigla_no_nome      # mesma regra de nome de arquivo

    agora = agora or datetime.now()
    return f"dados-{sigla_no_nome(sigla)}-{agora:%Y-%m-%d}.zip"
