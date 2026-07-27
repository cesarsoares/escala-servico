"""Exporta as telas renderizadas como HTML estático, com dados FICTÍCIOS.

Para quem vai avaliar o visual (designer, ferramenta de design) sem receber o
efetivo real da OM: monta um banco temporário com nomes inventados, renderiza
cada tela pelo próprio app e grava .html autocontido, com o CSS ao lado.

    python -m tools.exportar_interface [destino]

Padrão: ./export_interface (ignorado pelo git). Nada do banco real é tocado.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base, get_db
from app.domain.models import Cor
from app.main import app
from app.models.calendario import Feriado, OverrideDia
from app.models.escala import Escala, EscalaConcorrente, Participacao, Posto
from app.models.gestao import Auditoria
from app.models.impedimento import Impedimento
from app.models.militar import Militar
from app.models.referencia import OrganizacaoMilitar, PostoGraduacao
from app.models.servico import Permuta, Servico
from app.seeds import seed_circulos, seed_feriados, seed_postos_graduacao, seed_tipos_impedimento
from app.seeds.usuario import criar_ou_atualizar_gestor

RAIZ = Path(__file__).resolve().parent.parent
SENHA = "senha-de-demonstracao"
# Tudo o que é servido em /static: copiado para o lado e com o caminho
# reescrito, senão a página abre no navegador SEM estilo (ou sem a cortina de
# escalas) — e o defeito parece ser do app. Ao criar uma folha ou um script,
# acrescente aqui também.
ESTATICOS = ("style.css", "graficos.css", "impressao.css", "manual.css", "menu.js")

# Sobrenomes inventados, escolhidos para NÃO coincidir com o efetivo real.
NOMES = [
    "AVELAR", "BRANDÃO", "CALIXTO", "DORNELLES", "ESTEVÃO", "FURTADO", "GUIMARÃES",
    "HOLANDA", "IRACEMA", "JUCÁ", "KRUGER", "LOUREIRO", "MONTENEGRO", "NOVAES",
    "ORLANDO", "PEIXOTO", "QUEIRÓS", "RANGEL", "SEIXAS", "TAVORA", "URBANO",
    "VASCONCELOS", "WANDERLEY", "XIMENES", "YARA", "ZANOTTO", "ALCÂNTARA",
    "BITTENCOURT", "CAVALCANTI", "DELGADO", "ESPÍNDOLA", "FONTOURA", "GALVÃO",
    "HERINGER", "ITAMAR", "JORDÃO", "LACERDA", "MEIRELES", "NASCIMENTO", "OLIVEIRA",
]
OMS = [("Cmdo Ex Sul", "Comando do Exército do Sul"),
       ("1º BI Dem", "1º Batalhão de Infantaria de Demonstração"),
       ("2ª Cia Cmdo", "2ª Companhia de Comando"),
       ("B Adm Dem", "Batalhão de Administração de Demonstração")]

TELAS = {
    "01-consulta-calendario": "/?escala_id=1&ano={ano}&mes={mes}",
    "02-consulta-impressao": "/escalas/1/impressao?ano={ano}&mes={mes}",
    "03-login": "/gestao/login",
    "04-painel": "/gestao",
    # com ?ok=, para a tarja de confirmação aparecer na tela exportada
    "05-efetivo": "/gestao/militares?ok=militar-criado",
    "05b-efetivo-busca": "/gestao/militares?q=al",
    "06-militar-cadastro": "/gestao/militares/novo",
    "07-militar-edicao": "/gestao/militares/3",
    "08-escalas": "/gestao/escalas",
    "09-escala-detalhe": "/gestao/escalas/1",
    "10-escala-nova": "/gestao/escalas/nova",
    "11-calendario-gestao": "/gestao/calendario?ano={ano}",
    "12-impedimentos": "/gestao/impedimentos",
    "13-impedimentos-de-um-militar": "/gestao/impedimentos?militar_id=3",
    "14-escalar-periodo": "/gestao/escalar",
    "15-permutas-do-mes": "/gestao/permutas?escala_id=1&ano={ano}&mes={mes}",
    "16-permuta-registrar": "/gestao/permutas/servico/{servico_id}",
    "17-historico-auditoria": "/gestao/auditoria",
    "18-configuracoes": "/gestao/configuracao",
    "18b-config-graduacoes": "/gestao/configuracao/graduacoes",
    "18c-config-gestores": "/gestao/configuracao/gestores",
    "19-importar-historico": "/gestao/importar",
    "20-manual": "/manual",
}


def povoar(db: Session, hoje: date) -> int:
    """Banco de demonstração: gente, escalas e um mês fechado. Devolve um id de
    serviço (para a tela de registrar permuta)."""
    seed_circulos(db)
    seed_postos_graduacao(db)
    seed_tipos_impedimento(db)
    seed_feriados(db, range(hoje.year, hoje.year + 2))
    criar_ou_atualizar_gestor(db, "brigada", SENHA, "Sgt Demonstração")
    for i, (sigla, nome) in enumerate(OMS, start=1):
        db.add(OrganizacaoMilitar(id=i, sigla=sigla, nome=nome))
    db.flush()

    # Efetivo variado: oficiais e praças, com o cadastro em graus diferentes de
    # completude — é assim que a tela é vista na vida real.
    siglas = [s for (s,) in db.execute(
        select(PostoGraduacao.sigla).order_by(PostoGraduacao.ordem_hierarquica.desc())).all()]
    escolhidas = [s for s in siglas if s in
                  ("Ten Cel", "Maj", "Cap", "1º Ten", "2º Ten", "1º Sgt", "2º Sgt", "3º Sgt", "Cb")]
    for i, nome in enumerate(NOMES, start=1):
        sigla = escolhidas[i % len(escolhidas)]
        pg = db.scalar(select(PostoGraduacao).where(PostoGraduacao.sigla == sigla))
        completo = i % 3 != 0          # 1/3 fica incompleto, como na carga da planilha
        db.add(Militar(
            id=i, nome_guerra=nome, nome_completo=f"{nome} de Demonstração",
            posto_graduacao_id=pg.id, om_id=(i % len(OMS)) + 1,
            identidade=f"01{i:07d}" if completo else None,
            cpf=f"{i:011d}" if completo else None,
            data_promocao=date(2018 + i % 6, 1 + i % 12, 1 + i % 27) if completo else None,
            data_praca=date(2005 + i % 10, 1 + i % 12, 1 + i % 27) if completo else None,
            data_nascimento=date(1975 + i % 20, 1 + i % 12, 1 + i % 27) if completo else None,
            numero_antiguidade=(i * 7) % 500 + 1 if pg.circulo.eh_praca and completo else None,
            ativo=i != len(NOMES),     # um inativo, para a lista de inativos ter conteúdo
        ))
    db.flush()

    db.add(Escala(id=1, nome="Oficial de Dia ao Quartel", folga_minima_horas=48))
    db.add(Escala(id=2, nome="Guarda do Quartel", folga_minima_horas=48,
                  inicio_servico=time(18, 0), duracao_horas=14))
    db.add(Escala(id=3, nome="Plantão do Museu", tem_preta=False, folga_minima_horas=24))
    db.flush()
    db.add(Posto(id=1, escala_id=1, ordem=1))
    db.add(Posto(id=2, escala_id=2, ordem=1, rotulo="Comandante da Guarda"))
    db.add(Posto(id=3, escala_id=2, ordem=2, rotulo="Adjunto"))
    db.add(Posto(id=4, escala_id=3, ordem=1))
    db.add(EscalaConcorrente(escala_menor_id=1, escala_maior_id=2))
    for i in range(1, len(NOMES)):
        if i % 2:
            db.add(Participacao(militar_id=i, escala_id=1))
        if i % 3:
            db.add(Participacao(militar_id=i, escala_id=2))
        if i % 7 == 0:
            db.add(Participacao(militar_id=i, escala_id=3))
    # uma isenção permanente, para a tela mostrar o estado (regra 7.6)
    db.add(Participacao(militar_id=2, escala_id=1, ativo=False))
    db.commit()

    # Mês corrente fechado nas escalas 1 e 2; a 3 fica com buraco de propósito,
    # para o painel exibir o alerta de cobertura.
    from app.services import rotacao
    primeiro = hoje.replace(day=1)
    rotacao.escalar_e_gravar_periodo(db, 1, primeiro, primeiro + timedelta(days=45))
    rotacao.escalar_e_gravar_periodo(db, 2, primeiro, primeiro + timedelta(days=20))
    db.commit()

    # Impedimentos: um em curso, um futuro e um que conflita com dia escalado
    # (o alerta do painel).
    db.add(Impedimento(militar_id=5, tipo_impedimento_id=1,
                       inicio=hoje - timedelta(days=2), fim=hoje + timedelta(days=4),
                       observacao="dispensa médica"))
    db.add(Impedimento(militar_id=9, tipo_impedimento_id=2,
                       inicio=hoje + timedelta(days=12), fim=hoje + timedelta(days=42),
                       observacao="férias"))
    db.commit()

    servico = db.scalars(
        select(Servico).where(Servico.escala_id == 1, Servico.dia >= hoje)
        .order_by(Servico.dia)).first()
    conflito = db.scalars(
        select(Servico).where(Servico.escala_id == 1, Servico.dia > hoje + timedelta(days=6))
        .order_by(Servico.dia)).first()
    if conflito is not None:
        db.add(Impedimento(militar_id=conflito.militar_id, tipo_impedimento_id=3,
                           inicio=conflito.dia - timedelta(days=1),
                           fim=conflito.dia + timedelta(days=3),
                           observacao="curso"))

    # Uma permuta registrada, para a tela mostrar a cobertura (regra 9).
    outro = db.scalars(
        select(Servico).where(Servico.escala_id == 1, Servico.dia > hoje + timedelta(days=1))
        .order_by(Servico.dia)).first()
    if outro is not None:
        substituto = db.scalar(
            select(Participacao.militar_id).where(
                Participacao.escala_id == 1, Participacao.ativo.is_(True),
                Participacao.militar_id != outro.militar_id))
        db.add(Permuta(servico_id=outro.id, militar_substituto_id=substituto,
                       autorizado_por=1, observacao="troca acertada entre as partes"))

    db.add(Feriado(data=hoje + timedelta(days=9), nome="Aniversário da cidade (exemplo)"))
    db.add(OverrideDia(data=hoje + timedelta(days=16), cor=Cor.VERMELHA,
                       observacao="ponto facultativo (exemplo)"))
    # Auditoria com os três tipos de ação, para a tela de histórico ter conteúdo.
    db.add(Auditoria(usuario_id=1, entidade="militar", entidade_id=3, acao="alterar",
                     dados_antes={"nome_guerra": "CALIXTO", "cpf": None, "ativo": True},
                     dados_depois={"nome_guerra": "CALIXTO", "cpf": "00000000003", "ativo": True}))
    db.add(Auditoria(usuario_id=1, entidade="escala", entidade_id=2, acao="criar",
                     dados_depois={"nome": "Guarda do Quartel", "folga_minima_horas": 48}))
    db.add(Auditoria(usuario_id=1, entidade="permuta", entidade_id=1, acao="criar",
                     dados_depois={"servico_id": outro.id if outro else 1,
                                   "militar_substituto_id": 4}))
    db.commit()
    return servico.id if servico else 1


def exportar(destino: Path) -> None:
    hoje = date.today()
    destino.mkdir(parents=True, exist_ok=True)
    for arquivo in ESTATICOS:
        shutil.copy(RAIZ / "app/web/static" / arquivo, destino / arquivo)

    tmp = Path(tempfile.mkdtemp()) / "demo.sqlite3"
    engine = create_engine(f"sqlite:///{tmp}")
    Base.metadata.create_all(engine)
    db = Session(engine)
    servico_id = povoar(db, hoje)

    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as c:
            for nome, rota in TELAS.items():
                url = rota.format(ano=hoje.year, mes=hoje.month, servico_id=servico_id)
                if nome != "03-login":
                    c.post("/gestao/login", data={"username": "brigada", "password": SENHA},
                           follow_redirects=False)
                else:
                    c.cookies.clear()
                r = c.get(url)
                html = r.text
                for arquivo in ESTATICOS:
                    html = html.replace(f"/static/{arquivo}", arquivo)
                (destino / f"{nome}.html").write_text(html, encoding="utf-8")
                print(f"  {nome:<34} {r.status_code}  {len(html):>7} bytes  {url}")
    finally:
        app.dependency_overrides.clear()
        db.close()
        shutil.rmtree(tmp.parent, ignore_errors=True)

    (destino / "LEIA-ME.md").write_text(LEIA_ME, encoding="utf-8")
    print(f"\nExportado em: {destino}")


LEIA_ME = """# Interface do Sistema de Escala de Serviço — telas para avaliação

Páginas HTML **estáticas**, geradas pelo próprio sistema, com **dados
fictícios**: nomes, OMs e datas são inventados. Nenhum dado real de pessoal saiu
daqui. Abra qualquer `.html` no navegador; o CSS está na mesma pasta.

## O que é o sistema

Gera e controla as **escalas de serviço** de uma Organização Militar: quem entra
de serviço em cada dia, respeitando rotação, folga mínima e impedimentos. Usado
por um sargento gestor num desktop, e consultado por todo o efetivo.

Dois públicos, um só produto:
- **Consulta aberta**, sem login (`01`, `02`) — qualquer militar vê a escala.
- **Gestão**, com login (`03`–`17`) — um ou poucos gestores.

## As telas

| Arquivo | Tela |
|---|---|
| `01-consulta-calendario` | Panorama mensal — a página mais vista do sistema |
| `02-consulta-impressao` | Documento da escala do mês, para imprimir |
| `03-login` | Entrada da gestão |
| `04-painel` | Painel do gestor: cobertura, alertas, hoje/amanhã, cadastro, distribuição |
| `05-efetivo` | Lista do efetivo |
| `06`, `07` | Cadastro e edição de militar (com importação de ficha em PDF) |
| `08`, `09`, `10` | Escalas: lista, detalhe (postos, participantes, concorrência) e criação |
| `11-calendario-gestao` | Feriados e dias com cor forçada |
| `12`, `13` | Impedimentos: lista geral e ficha de um militar |
| `14-escalar-periodo` | Dispara o motor de rotação num período |
| `15`, `16` | Permutas do mês e registro de uma troca |
| `17-historico-auditoria` | Histórico de todas as alterações |
| `18-configuracoes` | OM da instalação, OMs, postos/graduações, tipos de impedimento e gestores |
| `19-importar-historico` | Carga do histórico de serviços em CSV (conferir → confirmar) |
| `20-manual` | Manual de uso — usa a folha do DOCUMENTO (`impressao.css` + `manual.css`), não a do sistema |

## Restrições que não são estéticas — por favor não "harmonizar"

1. **`impressao.css` é outra folha, de propósito** (tela `02`). Ela serve a uma
   impressora que **pode ser monocromática**: ali o dia de fim de semana/feriado
   é marcado pela letra **V** e por fundo cinza, nunca só por cor. Cor não
   sobrevive ao papel preto e branco.
2. **A cor vermelha do calendário é definida pela OM**: C:0 M:100 Y:100 K:0
   (`#FF0000`), no token `--vermelha-escala`. Não é escolha estética.
3. **Cor nunca é a única informação.** Estado, cor da escala e alerta sempre vêm
   com a palavra escrita ao lado. Monitor ruim e daltonismo são a regra, não a
   exceção.
4. **Densidade importa.** A escala maior tem 139 participantes; as tabelas são
   longas por natureza. Espaço demais obriga a rolar; de menos, cansa.
5. **Sem dependência externa.** A folha é servida pela própria aplicação, que
   roda numa rede interna, às vezes sem internet. Nada de CDN, fonte remota ou
   framework baixado em tempo de execução.

## Componentes novos nesta rodada (o que vale olhar primeiro)

Foram criados depois da entrega da linguagem visual, reusando os tokens do
`:root`. São os que ainda não passaram por revisão de design:

| Onde | Componente | O problema que resolve |
|---|---|---|
| `05-efetivo` | **Barra de busca** (`.formulario.busca`) | Achar uma pessoa entre 285 linhas |
| `05-efetivo` | **Estado vazio** (`.vazio`) | Busca sem resultado |
| todas as telas de gestão | **Tarja de confirmação** (`.sucesso`) | O sistema nunca dizia que a ação deu certo |
| `04-painel` | **Faixa de situação** (`.situacao.urgente` / `.calma`) | O urgente tinha o mesmo peso da estatística do ano |
| `09-escala-detalhe` | **Índice de seções** (`.secoes` + `.conta`) | Quatro assuntos numa página; com 139 participantes os outros somem |
| `12`, `13`, `16`, `09` | **`<optgroup>` por posto/graduação** | `<select>` com centenas de nomes em lista corrida |

A tarja de confirmação só aparece com `?ok=<chave>` na URL. Para vê-la, abra
`05-efetivo.html` — esta cópia foi gerada com a mensagem visível.

## Onde mexer

Os **tokens de design** estão no `:root`, no topo do `style.css` — tinta,
superfícies, cor institucional, semânticas e forma. É por ali que se começa.

O HTML aqui é **renderizado**: o fonte são templates Jinja2 em
`app/web/templates/` (mesma estrutura, com `{% %}` e `{{ }}`). Alterações de
marcação precisam voltar para lá.
"""


if __name__ == "__main__":
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else RAIZ / "export_interface"
    exportar(destino)
