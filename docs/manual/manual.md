Este manual está organizado **por tarefa** — o que você precisa fazer —, e não
por tela. Cada procedimento cita entre parênteses o número da regra
correspondente, para quem quiser conferir a fonte.

O sistema tem duas metades:

- **Consulta**, aberta a todos, sem login (regra 13.1). É onde qualquer militar
  vê a escala do mês e imprime o documento.
- **Gestão**, com login (regra 11). É onde se cadastra o efetivo, criam-se as
  escalas, fecha-se o mês e registram-se dispensas e permutas.

---

## 1. Instalando numa OM nova

Faça nesta ordem. Cada passo depende do anterior — e **o sistema conduz essa
sequência sozinho**: em **Gestão → Instalação** (o painel também aponta para lá
enquanto houver passo pendente) cada item mostra o que já está feito, o que
falta e o botão que leva à tela certa.

### 1.0 O primeiro acesso

Numa instalação recém-subida **não existe nenhum gestor**, e a gestão exige
login. Ao abrir `/gestao`, o sistema leva à tela de **primeiro acesso**: informe
um login (ex.: `brigada`), o nome e uma senha de ao menos 8 caracteres. Você
entra já logado, e a tela **se fecha permanentemente** — daí em diante, novos
gestores são cadastrados em **Configurações → Gestores**.

> **Anote a senha.** Não há recuperação por e-mail. Perdida a senha do único
> gestor, só a seção de TI recria o acesso, por linha de comando no servidor.
> É por isso que cadastrar um **segundo gestor** é um dos passos da instalação.

### 1.1 Dizer qual é a sua OM

Em **Configurações → Esta instalação**, escolha a OM desta instalação. Ela
aparece no cabeçalho e no rodapé de todas as telas. Se a sua OM ainda não está
na lista, cadastre-a em **Configurações → Organizações Militares** e volte.

Aproveite para preencher o **contato do suporte local** — é o que falta quando
algo dá errado e ninguém sabe a quem recorrer.

O sistema é **uma instalação por OM** (regra 13.2). Não há separação por OM
dentro do banco: cada OM sobe a sua.

### 1.2 Conferir postos e graduações

A tabela já vem com a escala hierárquica da **Lei 6.880/80, art. 16**. Se a sua
OM não usa alguma graduação, **desative** (não exclua). Se falta alguma, use
*Acrescentar* e diga **abaixo de qual** ela entra.

> A ordem dessa lista é o **primeiro critério de desempate da fila** (regra
> 9.1). Mexer nela muda quem entra de serviço primeiro daqui em diante — os
> serviços já gravados não mudam.

### 1.3 Cadastrar as OMs de origem

Num QG, o efetivo vem de várias OMs (regra 3.2). Cadastre todas em
**Configurações → Organizações Militares** antes de cadastrar as pessoas.

### 1.4 Cadastrar o efetivo

Em **Efetivo → Novo militar**. Há dois caminhos:

- **digitar** os dados; ou
- **importar a ficha individual em PDF** (SiCaPEx ou SCGPE), que apenas
  **pré-preenche o formulário** — nada é gravado até você conferir e salvar.

O **número de antiguidade** das praças (regra 9.5) **não existe em nenhuma das
duas fichas** e é sempre digitado à mão. Sem ele, o desempate entre praças da
mesma graduação roda sem o critério que a regra manda.

### 1.5 Criar as escalas

Em **Escalas → Nova escala**. Uma escala carrega:

| Campo | O que significa |
|---|---|
| Cores em que roda | preta (dias úteis), vermelha (sábados, domingos e feriados), ou as duas (regra 4.5) |
| Postos | quantas vagas a escala tem por dia (regra 2.5). O Oficial de Dia tem 1; a guarda pode ter 12 |
| Participantes | quem concorre na fila daquela escala (regra 3.3) |
| Concorrentes | as outras escalas que "conversam" com esta pela folga (regra 7.4.1) |
| Folga mínima | horas que o militar precisa completar antes de assumir de novo. Piso rígido de 24h; sugestão 48h (regra 7.2) |
| Janela do serviço | hora de início e duração. Padrão 08:00 por 24h (regra 2.4) |

**Concorrência é simétrica**: declarada de um lado, vale nos dois.

**Militar que só serve em fim de semana.** Na escala que roda as duas cores,
cada participante tem a coluna **Concorre em**: *as duas*, *só preta* ou *só
vermelha* (regra 3.3.1). É o caso do militar cuja função o impede de servir em
dia útil — ele participa da escala, mas só entra na fila da vermelha. Duas
coisas que costumam confundir:

- **não é isenção** (essa tira da escala inteira) **nem dispensa** (essa é por
  período e guarda a vez). Quem concorre só na vermelha simplesmente não está na
  fila da preta;
- **a folga mínima continua valendo em qualquer cor**: quem serviu no sábado só
  assume de novo depois de cumprir a folga, inclusive nas escalas concorrentes.

Quando a restrição deixa uma cor sem gente suficiente para as vagas, o painel
avisa dizendo **qual** cor está descoberta.

### 1.6 Carregar o histórico

Se a OM já vinha fazendo escala em planilha, **importe o histórico** em
**Configurações → importar o histórico de serviços**. Sem isso, o motor começa
com todo mundo empatado em "nunca serviu", e a primeira escalação sai só pela
antiguidade — ignorando quem acabou de deixar o serviço.

Como funciona:

1. **Baixe o modelo.** Ele sai preenchido com os nomes reais das suas escalas e
   do seu efetivo — é assim que você acerta o cabeçalho de primeira.
2. Preencha uma linha por **militar-dia**: `escala;data;militar;posto;om`.
   A coluna *posto* só é necessária quando a escala tem mais de uma vaga; a
   coluna *om* só quando há dois militares com o mesmo nome de guerra.
3. **Confira.** O sistema lê o arquivo e mostra o que entra, o que entra com
   ressalva e o que foi recusado, **com o motivo e o número da linha**. Nada é
   gravado nesta etapa.
4. **Confirme.** Só então os serviços são gravados.

Nada é resolvido no palpite: militar que não casa, escala desconhecida ou data
ilegível são **recusados com o motivo**, para você corrigir o arquivo.

### 1.7 Cadastrar os outros gestores

Em **Configurações → Gestores**. A regra 11 prevê **múltiplos gestores**, e todo
o histórico registra o nome de quem fez cada alteração.

Gestor **não se exclui** — o histórico aponta para ele e perderia o nome do
responsável por cada mudança. Desativar tira o acesso e preserva o registro.

---

## 2. O dia a dia

### 2.1 Fechar o mês

**Escalar período**: escolha a escala, o início e o fim, e mande escalar. O
motor percorre dia a dia e, em cada um:

1. classifica o dia em **preta** ou **vermelha** (regra 5);
2. monta a fila da cor, **do mais folgado para o menos** (regra 6.2);
3. **pula** quem está impedido, mas ele **mantém a vez** na fila (regra 7.5);
4. **pula** quem ainda não completou a folga mínima, contada do término do
   último serviço em **qualquer cor e qualquer escala concorrente** (regra 7.4);
5. desempata pela antiguidade (regra 9) e grava tantos militares quantos forem
   os postos do dia.

A operação é **idempotente**: rodar de novo não duplica os dias já gravados.

### 2.2 Lançar uma dispensa, férias, curso ou operação

No **Efetivo**, ao lado da pessoa, clique em **impedimentos** — abre a ficha
dela, já apontada, e não a lista geral (numa OM de centenas, cair na lista
geral obrigaria a procurar de novo).

Informe o tipo e o período. O militar passa a ser **pulado** no período, mas
**mantém a vez**: a fila não muda de ordem por causa disso (regra 7.5).

> **Se o mês já estava fechado**, lançar o impedimento **não** refaz a escala. O
> painel avisa quem está *escalado E impedido no mesmo dia*; a saída é voltar em
> **Escalar período** e re-escalar com a opção **regravar**.
>
> Atenção: *regravar* apaga o período e refaz — **as permutas do período são
> perdidas** e precisam ser refeitas. A tela lista o que foi perdido, e fica
> tudo no histórico.

### 2.3 Registrar uma permuta

Em **Permutas**, escolha a escala e o mês, clique no dia e informe quem cobre.

Permuta é **registro puro** (regra 9): anota quem cobriu o serviço. **A folga
continua sendo de quem estava escalado** — não há retribuição automática nem
recálculo de fila. Por isso o documento impresso mostra os dois nomes, em vez de
trocar um pelo outro.

A permuta é **negada** se ferir a folga mínima de quem vai cobrir. A recusa vem
com o motivo: negar é informação, não erro de sistema.

### 2.4 Feriados e cor forçada

Em **Calendário**:

- **feriados nacionais** já vêm embutidos; acrescente os da OM (regra 5.2);
- **forçar a cor de um dia** funciona nos dois sentidos (regra 5.3): um dia útil
  pode ser declarado vermelho, e um feriado trabalhado pode ser declarado preto.
  Registre a observação — ela fica no histórico.

### 2.5 Militar que sai da OM

No **Efetivo**, **desative**. Ele sai da rotação e o histórico é preservado. Não
existe exclusão de militar: apagá-lo levaria junto os serviços que ele prestou,
e com eles a folga que deles decorre.

Para tirar alguém de **uma escala só** (isenção permanente, regra 7.6), abra a
escala e **isente** o participante — o vínculo é desativado, não apagado, e
reincluir depois reaproveita o mesmo vínculo.

---

## 3. Consultar e imprimir

### 3.1 A consulta aberta

A página inicial mostra o **calendário do mês** de uma escala, com o
posto/graduação e o nome de quem serve em cada dia. Não exige login (regra 13.1)
— é para todo o efetivo consultar.

Para trocar de escala, clique na aba **ESCALAS**, na borda esquerda da tela, na
altura do meio: o menu desliza mostrando todas as escalas, e a que está aberta
aparece destacada.

**O menu fica aberto até você fechar** — clicar numa escala não o recolhe, o que
permite passar de uma para outra sem reabrir toda vez. Para fechar, clique na
aba de novo (ou tecle `Esc`). Com o menu fechado, o nome da escala em exibição
continua no título, acima do mês. Numa OM com muitas escalas, a lista rola
dentro do próprio menu.

Os dias de **escala vermelha** aparecem na cor da OM. A cor nunca é a única
informação: sábado e domingo vêm escritos no cabeçalho, e o feriado tem marca
própria.

### 3.2 O documento impresso

No calendário, **Imprimir**. O navegador imprime ou salva em PDF.

A folha de impressão é preparada para **impressora monocromática**: o dia
vermelho é marcado pela letra **V** (e `*` para feriado), não apenas por cor.
Havendo permuta, o documento mostra o escalado **e** quem cobre.

### 3.3 O painel

A tela inicial da gestão responde, nesta ordem:

1. **Cobertura** — até quando cada escala está fechada e quantos dias dos
   próximos 30 estão descobertos. Só conta o dia em que a escala realmente
   roda: uma escala só-vermelha não acusa buraco em dia útil.
2. **Precisa de você agora** — militar escalado E impedido no mesmo dia, dia
   gravado com menos militares que postos (regra 7.8), escala com menos
   participantes que vagas.
3. **Hoje e amanhã** — quem serve, com a permuta ao lado. Se amanhã não está
   fechado, mostra quem o motor escalaria.
4. **Cadastro do efetivo** — quantos ainda estão sem data de promoção, sem data
   de nascimento e quantas praças estão sem número de antiguidade. Sem esses
   campos o desempate roda sem os critérios que a regra manda.
5. **Distribuição no ano** — mínimo, máximo e quantos nunca serviram, por
   escala.

### 3.4 O histórico

Em **Histórico** ficam **todas** as alterações manuais (regra 11), com filtros
por tipo de registro, ação, gestor e período. Em uma alteração, a tela mostra
**só o campo que mudou**.

---

## 4. Perguntas que aparecem sempre

**Escala preta e escala vermelha, qual é a diferença?**
Preta são os serviços dos **dias úteis**; vermelha, os de **sábados, domingos e
feriados** (regras 2.5 e 2.6). Cada escala roda as duas filas em paralelo, e
quem serve numa não perde a vez na outra.

**O que é um "posto"?**
Cada **vaga** de uma escala num dia. Cuidado com a ambiguidade: "posto/graduação"
(patente) é outra coisa — no sistema, sempre que for patente está escrito
"posto/graduação".

**E o "quarto"?**
Subdivisão de um posto revezada por vários militares no mesmo dia. É detalhe
operacional e **não muda a folga**: quem serviu no dia, ainda que num quarto,
ganha a folga cheia.

**Quem é "o mais folgado"?**
Quem serviu há mais tempo *naquela cor*. É o topo da fila. O motor pega os N
mais folgados disponíveis, sendo N o número de postos do dia.

**Por que fulano foi pulado?**
Três motivos possíveis: está **impedido** no dia; ainda **não completou a folga
mínima** (contada de qualquer escala concorrente, não só desta); ou **não é
participante ativo** da escala.

**Não tem gente suficiente para respeitar a folga. E agora?**
O sistema **avisa**; a decisão é do gestor (regra 8) e tipicamente é extinguir a
escala. Extinguir é lógico: ela sai da rotação e os serviços gravados ficam.

**Mudei a folga mínima da escala. Recalcula o que já foi escalado?**
Não. Vale da próxima escalação em diante.

**Posso apagar uma escala?**
Não; **extinga**. Apagar levaria junto os serviços gravados nela e a folga que
deles decorre. O mesmo vale para um posto que já tenha serviço gravado.

**Perdi a senha do único gestor.**
A TI local recria pelo terminal, com
`python -m app.seeds.usuario <login> "<nome>"`. Por isso o sistema não deixa
desativar o último gestor ativo pela tela.

---

## 5. Fora do escopo

**Escala de representação** — designar N militares para um evento pontual, tipo
palestra — **não** faz parte desta versão. Não é rotação, não tem preta e
vermelha, e não gera folga.
