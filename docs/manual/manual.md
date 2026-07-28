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

### 1.0.1 A senha de instalação

Na primeira vez, a tela de primeiro acesso pede uma **senha de instalação**.
Ela não é escolhida por ninguém: o sistema a gera sozinho no primeiro boot e a
guarda em `dados/primeiro-acesso.txt`, no servidor. Também aparece no log de
quando o sistema subiu.

Ela existe porque, enquanto não há gestor, essa tela está aberta a **qualquer
pessoa que alcance o endereço do sistema** — numa rede de OM, o efetivo inteiro.
A senha prova que quem está criando o administrador tem acesso ao servidor.

Peça-a a quem instalou. Ela **some sozinha** assim que o gestor é criado, e não
serve para mais nada depois disso. Perdida antes? Apague o arquivo e recarregue
a página: nasce outra.

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
**Configurações → Importar histórico**. Sem isso, o motor começa
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

Na aba **Escalar**: escolha a escala, o início e o fim, e mande escalar. O
motor percorre dia a dia e, em cada um:

1. classifica o dia em **preta** ou **vermelha** (regra 5);
2. monta a fila **daquela cor**, do mais folgado para o menos (regra 6.2) —
   quem não concorre nessa cor nem entra na fila (regra 3.3.1);
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

Os **tipos** (dispensa, férias, curso, operação…) variam de OM para OM e são
editáveis em **Configurações → Tipos de impedimento**. Tipo que a OM deixou de
usar se **desativa**, não se apaga — os impedimentos antigos apontam para ele.

> **Se o mês já estava fechado**, lançar o impedimento **não** refaz a escala. O
> painel avisa quem está *escalado E impedido no mesmo dia*; a saída é voltar na
> aba **Escalar** e re-escalar com a opção **regravar**.
>
> Atenção: *regravar* apaga o período e refaz — **as permutas do período são
> perdidas** e precisam ser refeitas. A tela lista o que foi perdido, e fica
> tudo no histórico.

### 2.3 Registrar uma permuta

Em **Permutas**, escolha a escala e o mês. A tela lista os dias com serviço;
clique em **permutar** na linha do dia e escolha quem cobre. O substituto sai
dos participantes ativos daquela escala.

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

### 2.6 Backup: guardar a escala e recuperá-la

O sistema roda **no servidor da sua OM**. Não existe cópia em lugar nenhum além
da que alguém baixou — e por isso isto é tarefa do dia a dia, não de instalação.

**Guardar.** Em **Configurações → Backup e restauração**, clique em *Baixar
backup agora*. Vem um arquivo `.sqlite3` com **tudo**: efetivo, escalas,
serviços, impedimentos, permutas, calendário, gestores, histórico e as próprias
configurações. Guarde-o **fora deste servidor** — backup no mesmo disco que se
perder não salva ninguém. O bom hábito é baixar **sempre que fechar o mês**: é
aí que há trabalho novo a perder. Se passar de 30 dias, o sistema cobra na tela
de Configurações.

Baixar não atrapalha ninguém: a consulta continua respondendo enquanto a cópia é
feita.

**Recuperar.** Na mesma tela, envie o arquivo em *Restaurar*. O sistema
**confere antes** e mostra de que OM é o backup, quantos militares e serviços
tem, até quando vai e quais gestores existem lá dentro. Nada muda até você
confirmar.

Três coisas para saber antes de confirmar:

- restaurar **substitui tudo**, inclusive gestores e senhas. O que foi lançado
  depois da data do backup se perde;
- se o **seu login não existir** no backup, você perde o acesso à gestão. A tela
  avisa — e deixa seguir, porque às vezes é exatamente o que se quer. Nesse
  caso, quem entra depois é um dos gestores listados;
- o banco que estava em uso **não é apagado**: fica guardado ao lado, com a data
  no nome (`escala-antes-da-restauracao-...`). Se algo der errado, a TI
  recoloca esse arquivo.

**As cópias automáticas.** O sistema guarda sozinho **uma por dia**, em
`dados/backups`, mantendo as **últimas 7**. Elas existem para o dia em que a
máquina dá problema e ninguém baixou nada na semana. Ficam no mesmo disco do
banco: resolvem erro humano e troca de máquina, **não** resolvem disco perdido —
para isso é preciso que o arquivo tenha saído do servidor.

Na tela, cada cópia tem um botão *baixar*. É por ali que "o estado de ontem" sai
da máquina que está morrendo. O botão *Gerar a cópia de hoje agora* serve para
quem vai desligar o servidor em seguida e não pode esperar.

**Exportar para planilha** é outra coisa, no mesmo lugar. Sai um `.zip` com os
dados em CSV para abrir no Excel: efetivo, escalas, histórico de serviços,
impedimentos, permutas, calendário e o histórico de alterações. **Não restaura o
sistema** — serve para conferir, prestar informação, ou levar o histórico para
outra instalação (o `servicos.csv` está no formato que a tela *Importar
histórico* lê). CPF e identidade só entram se você marcar a caixa.

### 2.7 Trocar de máquina

O caso real: *"este servidor está com problema, precisamos subir outro com o
estado de ontem"*. Há dois caminhos, e o primeiro é sempre melhor quando dá.

**Caminho curto — copiar a pasta `dados/`.** Com acesso ao servidor antigo:

1. pare o sistema: `docker compose stop`;
2. copie a pasta **`dados/` inteira** para a máquina nova;
3. lá, suba com o mesmo `docker compose up -d`.

A pasta leva o banco, a **chave de sessão** e as cópias automáticas de uma vez.
Ninguém é deslogado e não há o que conferir: é a mesma instalação, noutro
hardware.

> No Linux com Docker, os arquivos dentro de `dados/` pertencem ao **root** (é
> o usuário do container). Copiar exige `sudo cp -a dados/ /destino/` — e o
> `-a` preserva dono e permissões, que importam: a chave de sessão e a senha de
> primeiro acesso são gravadas como legíveis só pelo dono.

**Caminho longo — só o arquivo de backup.** Quando o servidor antigo já não
sobe, ou só sobrou o `.sqlite3` que alguém baixou:

1. instale o sistema na máquina nova (`docker compose up -d`);
2. abra `/gestao`. Como não há gestor, ele leva ao **primeiro acesso** — ali,
   escolha **"restaure a partir dele"**, não crie acesso novo;
3. envie o arquivo, com a **senha de instalação** da máquina nova (item 1.0.1 —
   ela está em `dados/primeiro-acesso.txt`, no servidor novo, não no antigo). O
   sistema mostra de que OM é o backup, quantos militares e serviços tem, **até
   quando vai** e qual versão o gerou;
4. confirme e entre com **o login e a senha de sempre** — as senhas vêm dentro
   do backup.

Se você criar um gestor antes de restaurar, ele será apagado pela restauração:
o backup traz os gestores dele. Por isso a ordem acima.

**Três coisas que o backup não leva** e que precisam ser conferidas na máquina
nova:

- **a chave de sessão** (`dados/secret_key`) — de propósito. A máquina nova gera
  a sua, e todo mundo precisa entrar de novo. As **senhas continuam as mesmas**;
- **o fuso horário** (`TZ` no `docker-compose.yml`). Sem ele, o Histórico volta a
  mostrar os horários 3h adiantados;
- **a versão do sistema.** Se a máquina nova rodar uma imagem **mais antiga** que
  a de origem, o backup é **recusado** — atualize a imagem antes. O contrário
  funciona: backup mais antigo é aceito e atualizado na hora.

**Até onde vai o que foi restaurado?** A conferência mostra a *última alteração
registrada* e o *último dia escalado* do arquivo. Tudo o que foi lançado no
servidor antigo depois disso se perdeu — confira o mês corrente no Painel antes
de seguir escalando.

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

No calendário, **Versão para impressão** (ao lado do mês). O navegador imprime
ou salva em PDF. O endereço é aberto como o resto da consulta — dá para mandar
o link a quem precise do documento.

A folha de impressão é preparada para **impressora monocromática**: o dia
vermelho é marcado pela letra **V** (e `*` para feriado), não apenas por cor.
Havendo permuta, o documento mostra o escalado **e** quem cobre.

### 3.3 O painel

A tela inicial da gestão responde, **nesta ordem** — o que faz alguém deixar de
entrar de serviço vem antes de qualquer estatística:

1. **Precisa de você agora** — o resumo do que está pendente, com link para cada
   assunto. (Enquanto a instalação não estiver completa, acima dele aparece a
   faixa **Instalação em andamento**, com o próximo passo.)
2. **Cobertura das escalas** — até quando cada escala está fechada e quantos dias
   dos próximos 30 estão descobertos. Só conta o dia em que a escala realmente
   roda: uma escala só-vermelha não acusa buraco em dia útil.
3. **Exige atenção** — militar escalado E impedido no mesmo dia, dia gravado com
   menos militares que postos (regra 7.8) e escala com menos participantes que
   vagas — esta última **por cor**, quando alguém concorre só numa (regra 3.3.1).
4. **Serviço de hoje** e **Amanhã** — quem serve, com a permuta ao lado. Se
   amanhã ainda não está fechado, mostra quem o motor escalaria.
5. **Completude do cadastro** — quantos ainda estão sem data de promoção, sem
   data de nascimento e quantas praças estão sem número de antiguidade. Sem
   esses campos o desempate roda sem os critérios que a regra manda.
6. **Distribuição no ano** — mínimo, máximo e quantos nunca serviram, por escala.
7. **Dias vermelhos à frente** — os fins de semana e feriados da janela, que são
   onde a escala costuma apertar.
8. **Últimas alterações** — as oito mais recentes; o resto fica no Histórico.

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
Quatro motivos possíveis: está **impedido** no dia; ainda **não completou a
folga mínima** (contada de qualquer escala concorrente, não só desta); **não
concorre naquela cor** (regra 3.3.1 — confira a coluna *Concorre em* na escala);
ou **não é participante ativo** da escala.

**Como acho alguém no efetivo?**
Pela busca no alto da tela **Efetivo**: ela casa o nome de guerra **ou** o nome
completo, sem diferenciar maiúsculas, e dá para filtrar por posto/graduação e
por OM. O total à direita ("2 de 285") nunca mente sobre o tamanho do efetivo.

**Não tem gente suficiente para respeitar a folga. E agora?**
O sistema **avisa**; a decisão é do gestor (regra 8) e tipicamente é extinguir a
escala. Extinguir é lógico: ela sai da rotação e os serviços gravados ficam.

**Mudei a folga mínima da escala. Recalcula o que já foi escalado?**
Não. Vale da próxima escalação em diante.

**Posso apagar uma escala?**
Não; **extinga**. Apagar levaria junto os serviços gravados nela e a folga que
deles decorre. O mesmo vale para um posto que já tenha serviço gravado.

**Com que frequência devo baixar o backup?**
Ao fechar cada mês, no mínimo. Passados 30 dias sem baixar, o cartão *Backup e
restauração* em Configurações passa a cobrar. Guarde o arquivo fora do servidor.

**Alguém apagou o mês errado. Dá para voltar?**
Só a partir de um backup (item 2.6). Não há "desfazer": o **Histórico** registra
quem fez o quê, mas não reverte. É a razão de o backup ser tarefa de rotina.

**A exportação em CSV serve de backup?**
Não. Ela é para **ler** os dados fora do sistema — Excel, conferência, prestação
de informação. Quem restaura a instalação é o arquivo `.sqlite3`. O
`servicos.csv` da exportação, esse sim, entra numa instalação nova pela tela
*Importar histórico*.

**Perdi a senha do único gestor.**
A TI local recria pelo terminal do servidor, com

```
docker compose exec app python -m app.seeds.usuario <login> "<nome>"
```

O comando **cria ou troca a senha** do login informado. A tela de primeiro
acesso não serve para isso: ela se fecha assim que existe um gestor, justamente
para não virar um cadastro aberto. Por isso o sistema também não deixa desativar
o último gestor ativo — e por isso o segundo gestor é um passo da instalação.

---

## 5. Fora do escopo

**Escala de representação** — designar N militares para um evento pontual, tipo
palestra — **não** faz parte desta versão. Não é rotação, não tem preta e
vermelha, e não gera folga.
