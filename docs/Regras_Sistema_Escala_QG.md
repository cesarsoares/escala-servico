# Sistema de Escala de Serviço

## Documento de Regras para Validação

**Versão:** rascunho 2 · validado pelo Sargento Brigada; escopo ampliado para qualquer OM
**Objetivo deste documento:** descrever, em linguagem clara e sem termos de programação, todas as regras que o sistema deve seguir. Ele deve ser lido e conferido regra por regra. Cada regra tem um número (ex.: `6.3`) para facilitar apontar correções. Nada aqui está codificado ainda — este é o passo de pegar erro barato antes de construir.

> **O que mudou da versão 1 para a 2** (após o OK do Brigada e a decisão de atender também os batalhões):
> - O sistema deixa de nascer com **cinco escalas fixas**; a escala vira uma **entidade que o gestor cria e exclui** (seções 2 e 4).
> - Uma escala pode ter **vários militares por dia** (postos), não apenas um; um posto pode ser dividido em **quartos** (seções 2 e 6).
> - A **folga mínima** deixa de ser 48h fixas: é **configurável por escala**, com **piso rígido de 24h** (seção 7).
> - As **escalas concorrentes** passam a ser **declaradas explicitamente** e a relação é **simétrica** (seção 7).
> - A escala tem **ciclo de vida** (ativa/extinta); efetivo insuficiente para o piso leva o gestor a decidir, tipicamente **extinguindo** a escala (seções 4 e 7).
> - A **janela do serviço** (horário de início + duração) passou a ser **configurável por escala** — o 08:00/24h vira apenas o default (seções 2.4 e 4.2).
> - O sistema **não é multitenant**: uma instalação por OM (seção 17.6).

---

## 1. Escopo

- **1.1** O sistema atende **qualquer Organização Militar (OM)** — do QG do CMS a batalhões — e por isso **não nasce com um conjunto fixo de escalas**. O gestor **cria, configura e exclui** as escalas de cada OM (seção 4). As escalas do QG (Superior de Dia à Guarnição, Oficial de Dia ao QG, Adjunto do Oficial de Dia, Permanência do Portão e Museu) são apenas o **exemplo de partida**, não o limite.
- **1.2** Como o escopo abrange batalhões, o sistema precisa suportar escalas de **tropa** (ex.: guarda do quartel, plantão de alojamento, reforço de guarda), que têm **vários militares por dia** e efetivo que varia com o licenciamento do serviço militar obrigatório.
- **1.3** A **escala de representação** (designação de militares para eventos, como palestras) fica para uma **segunda fase**. Ela é descrita na seção 15 apenas para registro, e seu desenho não altera a primeira versão. No vocabulário do RISG (Dec. 42.018/1957), a rotação da v1 corresponde aos **serviços gerais**; a representação corresponde aos **serviços extraordinários**.
- **1.4** O sistema roda como aplicação web dentro de um container Docker, pensado para funcionar na rede interna do quartel.

---

## 2. Conceitos fundamentais

- **2.1 Militar:** pessoa cadastrada no sistema, com posto/graduação, nome, OM de origem e dados de antiguidade.
- **2.2 Escala:** um serviço rotativo (ex.: Oficial de Dia ao QG, Guarda do Quartel). É uma **entidade que o gestor cria e exclui** (seção 4). Cada escala tem sua própria lista de participantes, seu número de postos, sua folga mínima e suas escalas concorrentes.
- **2.3 Participante:** militar que concorre a uma escala específica. Um militar pode participar de mais de uma escala.
- **2.4 Serviço:** um turno que **pertence ao dia em que começa** — é esse dia que define se ele é preta ou vermelha. O padrão é **24 horas começando às 08:00** (termina às 08:00 do dia seguinte), mas o **horário de início e a duração são configuráveis por escala** (regra 4.2): existem escalas de outra janela, p. ex. **18:00 às 08:00** (14h). A folga é sempre contada a partir do **término**, então mudar a janela muda a conta da folga (seção 7).
- **2.5 Posto:** cada **vaga** de uma escala num dado dia. Uma escala pode ter **um posto** (ex.: Oficial de Dia) ou **vários** (ex.: guarda do quartel com 12 ou mais). O número de postos é definido pelo gestor conforme a OM e a área a cobrir.
- **2.6 Quarto:** subdivisão interna de um posto ao longo das 24h, quando ele é revezado por mais de um militar no mesmo dia (ex.: um posto de reforço coberto por 3 militares em quartos). O quarto é **detalhe operacional** — quem cobre qual pedaço do dia — e **não altera a contagem de folga** (ver 7.7).
- **2.7 Escala preta:** os serviços dos **dias úteis**.
- **2.8 Escala vermelha:** os serviços de **sábados, domingos e feriados**.
- **2.9** Cada escala roda **duas filas independentes em paralelo**: uma preta (dias úteis) e uma vermelha (fins de semana e feriados). Uma escala pode operar **só em uma das cores** (ex.: Museu, só vermelha).
- **2.10** Em cada dia, cada escala tem **tantos militares de serviço quantos forem seus postos** naquele dia. A unidade de escalação é o **militar-dia**: quem entra num posto (ou num quarto de um posto) naquele dia conta como servindo o dia.

---

## 3. Cadastro do militar

- **3.1** Cada militar guarda: nome de guerra, nome completo, posto/graduação, OM de origem.
- **3.2** Para o desempate por antiguidade (seção 9), o cadastro guarda também: **data de promoção** ao posto atual, **data de praça** e um campo livre para **notas de curso / observação de antiguidade** (usado só no último critério de desempate).
- **3.2.1 Antiguidade das praças de tropa:** na incorporação, cada soldado recebe um **número de antiguidade** dado pela OM — é esse número que estabelece a antiguidade dele. Para praças, esse número é **informado no cadastro** do militar no sistema e é o critério de desempate da graduação (seção 9).
- **3.3** Para cada escala em que participa, o militar tem duas datas: **data do último serviço na preta** e **data do último serviço na vermelha**. São essas datas que ordenam as filas.
- **3.4 Migração inicial:** ao implantar o sistema, as datas de último serviço serão definidas **a critério do Sargento Brigada**, para ninguém perder a antiguidade de serviço já acumulada na planilha.

---

## 4. Escalas — criação, configuração e ciclo de vida

- **4.1** A escala é uma **entidade que o gestor cria e exclui** a critério do comandante, tendo o **Sargento Brigada / sargenteante** como operador. Não há um conjunto fixo embutido no sistema.
- **4.2 Ao criar uma escala, o gestor define:**
  - **nome** (ex.: Guarda do Quartel);
  - em quais **cores** ela opera (preta, vermelha ou ambas);
  - o número de **postos** por dia (quantos militares servem por dia) — fixo por escala; se uma OM precisa de quantidades diferentes, cria escalas diferentes;
  - a **janela do serviço**: **horário de início** e **duração em horas** (seção 2.4). O padrão é 08:00 por 24h, mas há escalas que começam às 18:00 e vão até as 08:00 (14h), etc. — por isso é configurável por escala;
  - a lista de **militares participantes**;
  - as **escalas concorrentes** dela (seção 7);
  - a **folga mínima** em horas (seção 7), respeitado o piso de 24h.
- **4.3 Efetivo mínimo:** o número de participantes precisa ser suficiente para sustentar os postos respeitando a folga mínima. Exemplo do Brigada: uma escala de **reforço da guarda** com **1 posto dividido em quartos por 3 militares** exige **no mínimo 6 participantes** — três servem no dia, três folgam, garantindo a folga de 24h. O sistema **avisa** quando o efetivo não fecha (ver 7.8).
- **4.4 Ciclo de vida:** a escala pode estar **ativa** ou **extinta**, com datas de vigência. Quando o efetivo cai a ponto de não haver como cumprir o piso de folga, o sistema avisa e o gestor decide — o desfecho típico é a **extinção da escala**.
- **4.5 Exemplo de partida (QG do CMS):** Superior de Dia à Guarnição (oficiais superiores e capitães), Oficial de Dia ao QG (subalternos), Adjunto do Oficial de Dia (1º/2º Sgt), Permanência do Portão (3º/4º Sgt) — todas 1 posto, preta e vermelha — e Museu (1 posto, só vermelha). Essa relação descreve a prática atual do QG; **não** é uma configuração fixa: outra OM monta as suas.
- **4.6** A associação posto/graduação × escala é apenas a prática de quem participa; o sistema permite ao gestor ajustar livremente quem concorre a cada escala.

---

## 5. Classificação do dia (preta ou vermelha)

- **5.1** Por padrão, o sistema classifica sozinho: dia útil = **preta**; sábado, domingo ou feriado = **vermelha**.
- **5.2 Feriados:** o sistema já vem com os **feriados nacionais**. O gestor pode **acrescentar outros dias** (feriados estaduais, locais, datas comemorativas).
- **5.3 Override do comando:** o gestor pode declarar **qualquer dia** como vermelha, mesmo que seja dia útil. Ao fazê-lo, ele pode **escrever uma observação de controle** (ex.: motivo, autorização).
- **5.4 Fim de semana prolongado:** é apenas uma **sequência de dias vermelhos**, cada dia com um militar diferente. Exemplo: feriado na quinta + comando declara a sexta como vermelha → quinta, sexta, sábado e domingo são todos vermelhos, quatro militares distintos, todos puxados da fila vermelha.

---

## 6. Regra de rotação — quem é o próximo (o coração do sistema)

- **6.1** Para preencher os postos de um dia numa escala, o sistema:
  1. classifica o dia (preta ou vermelha) — seção 5;
  2. olha a fila correspondente daquela escala;
  3. percorre a fila do **mais folgado** para o menos folgado (ou seja, de quem serviu há mais tempo naquela cor para quem serviu mais recentemente);
  4. escolhe os **N primeiros que estiverem disponíveis** naquele dia, sendo **N o número de postos** da escala naquele dia (seção 7);
  5. registra os serviços e atualiza as datas (seção 8).
- **6.2** Quando a escala tem **1 posto**, "os N primeiros" vira "o primeiro" — é o caso das escalas de oficial. Quando tem vários postos (guarda), o sistema tira do topo da fila tantos militares quantos forem os postos.
- **6.3** Se um posto for **dividido em quartos** entre vários militares no mesmo dia, cada um desses militares ocupa uma vaga da fila naquele dia (todos contam como servindo o dia). A distribuição de qual militar cobre qual quarto é operacional e não muda a ordem da fila.
- **6.4 "Mais folgado"** significa **serviu há mais tempo** naquela cor — o topo da fila.
- **6.5 Empate** na data de último serviço é resolvido pela cadeia de antiguidade da seção 9.
- **6.6** A ordem da fila **nunca muda** por causa de impedimento. Quem está impedido é apenas **pulado naquele dia** e continua sendo o mais folgado — ele assume assim que estiver disponível (ver 7.5).

---

## 7. Disponibilidade e as duas folgas

- **7.1 Folga de rotação (natural):** é a consequência da fila. Com 7 participantes numa escala de 1 posto, cada um só volta depois que os outros 6 serviram — 6 de folga. Por isso a folga varia de escala para escala: depende de **quantos participam** e de **quantos postos** ela tem. O sistema não precisa configurar isso; ela cai naturalmente da rotação.
- **7.2 Folga mínima (configurável por escala, piso de 24 horas):** nenhum militar assume um novo serviço antes de completar a **folga mínima** da escala em que vai entrar, contada desde o **término** do serviço anterior.
  - **7.2.1** A folga mínima é um **parâmetro de cada escala**, definido pelo gestor. O **default sugerido é 48h** (prática das escalas de oficial do QG), mas o gestor pode configurá-la para menos — inclusive **24h para escalas de oficial** — quando o efetivo apertar.
  - **7.2.2 Piso rígido de 24h:** a folga mínima **nunca pode ser inferior a 24h**, para qualquer escala. Regra física inquebrável: quem **sai de serviço no dia X não assume outra escala no mesmo dia X**.
- **7.3** A folga é contada do **término** do serviço anterior até o **início** do próximo, e ambos dependem da **janela de cada escala** (regra 2.4). No caso padrão (24h a partir das 08:00), uma folga de 48h coloca o militar disponível a partir do **terceiro dia** (serviço na quinta → folga sexta e sábado → disponível no domingo); uma de 24h, a partir do **dia seguinte ao término**. Quando as escalas concorrentes têm janelas diferentes, o cálculo usa o término real do serviço anterior e o início real do próximo.
- **7.4 Escalas concorrentes (relação explícita e simétrica):** a folga mínima olha para o **serviço mais recente do militar em qualquer cor e em qualquer escala concorrente**. É isso que faz as escalas conversarem — servir numa escala bloqueia o militar nas concorrentes pelo período de folga.
  - **7.4.1** A concorrência é **declarada pelo gestor** por escala (ex.: Guarda do Quartel concorre com Plantão do Alojamento) e é **simétrica**: se A concorre com B, então B concorre com A.
  - **7.4.2** Quando um militar vai entrar numa escala, o piso aplicado é o **da escala em que ele está entrando**. Ex.: se a Guarda tem folga de 24h, o militar pode assumir a guarda 24h após o término de qualquer serviço concorrente, ainda que outra escala use 48h.
- **7.5 Impedimentos temporários** (o militar não está "pronto"): dispensa médica, férias, curso, operação, e situações afins, registradas como **períodos com início e fim**. Durante o período, o militar é pulado, mas **mantém a vez**. Exemplo: escalado para a quarta, mas em dispensa médica → não serve na quarta; quando voltar, se continuar o mais folgado, assume o primeiro serviço disponível. Exemplo dos 15 dias de férias com 7 na escala: os outros 6 servem enquanto ele está fora, empurram suas próprias datas para frente, e ele retorna naturalmente na cabeça da fila.
- **7.6 Isenção permanente:** para tirar um militar de uma escala de forma duradoura (ex.: chefe de seção, fiscal administrativo, militar na reserva), basta **removê-lo da participação** naquela escala. O sistema **não** modela "função" — se o militar não deve concorrer, ele simplesmente não é participante. A função registrada na antiga planilha não tem valor para a escala.
- **7.7 Quarto não muda a folga:** servir um **quarto** de um posto conta como servir o dia inteiro para efeito de folga. Mesmo quem cobriu só um pedaço das 24h ganha a folga cheia da escala — coerente com "saiu no dia X, não assume no dia X" (7.2.2).
- **7.8 Efetivo insuficiente para o piso:** se, num dia, **não houver participantes disponíveis suficientes** para preencher os postos respeitando a folga mínima (situação comum quando o licenciamento do serviço obrigatório reduz o efetivo), o sistema **não força** — ele **avisa o gestor** para decisão. O desfecho típico é a **extinção da escala** (4.4). O aviso e a decisão ficam registrados na auditoria (seção 12).

---

## 8. Registro do serviço realizado

- **8.1** Quando um militar é escalado e cumpre o serviço, o sistema **atualiza a data do último serviço** dele naquela cor (preta ou vermelha) e ele vai para o fim da fila correspondente.
- **8.2** Todo serviço realizado fica no **histórico** (seção 12).

---

## 9. Desempate por antiguidade

Quando dois militares empatam na data de último serviço, o próximo é o **mais moderno**, decidido nesta ordem:

- **9.1** **Posto/graduação** — o mais moderno vai primeiro. Exemplo: um Cap empatado com um Maj é mais moderno, então o **Cap serve antes**.
- **9.2** Se o posto for o mesmo: **data de promoção** (o promovido mais recentemente é o mais moderno).
- **9.3** Se ainda empatar: **data de praça**.
- **9.4** Se todas as anteriores forem iguais: o **Sargento Brigada decide** manualmente, com base nas **notas de curso**.
- **9.5 Praças de tropa:** para soldados (mesma graduação), o desempate usa o **número de antiguidade da incorporação** registrado no cadastro (regra 3.2.1), em vez de promoção/praça.

---

## 10. Trocas e permutas

- **10.1** Uma troca é quando dois militares combinam de **trocar quem fisicamente cumpre o serviço**. Ela é registrada apenas para **consulta futura**.
- **10.2** A folga **sempre segue quem estava escalado** (o creditado), **não** quem fisicamente cobriu. Exemplo: na vez do A, quem serve é o B; o serviço **conta para o A** (a data e a folga do A é que se movem); o B **não ganha folga** por isso.
- **10.3** A troca é **apenas registro** — o sistema **não agenda retribuição automática**. Que o João cobriu o Pedro hoje **não** obriga o Pedro a cobrir a próxima do João; a retribuição, se houver, é combinada entre eles e decidida pelo sargenteante/Brigada, e entra como um **novo registro** quando ocorrer. O sistema só guarda o histórico das trocas.
- **10.4** A troca **não recalcula** as datas de rotação; ela só fica registrada.
- **10.5 Barreira da folga mínima:** o pedido de troca é **negado** se fizer o militar que vai cobrir ferir a **folga mínima** da escala coberta (48h, 24h ou o que estiver configurado — seção 7). Exemplo com folga de 48h: o B serviu anteontem; ele não pode cobrir hoje. Para essa verificação, o sistema considera **quem vai cobrir de verdade** (a informação que fica no registro da permuta), não apenas quem está escalado no papel.

---

## 11. Previsão de escala × motor dinâmico

- **11.1** São **dois conceitos distintos**:
  - **Previsão (documento estático):** uma projeção que o gestor **fecha e publica** para que os concorrentes se planejem. Ela **não** se reescreve sozinha.
  - **Motor (dinâmico):** o cálculo sempre atualizado de "quem é o mais folgado disponível hoje", que se reequilibra conforme entram dispensas e apresentações.
- **11.2** O gestor mantém a previsão **na mão**, ajustando conforme militares entram ou saem da escala.
- **11.3 Alerta de divergência:** quando a realidade fura a previsão publicada (ex.: uma dispensa médica não prevista surge no dia 10), o sistema **avisa** que a previsão não bate mais com o motor a partir do ponto afetado. O gestor decide se **re-fecha** a previsão.

---

## 12. Histórico e auditoria

- **12.1** O sistema mantém histórico de todos os **serviços realizados**, por escala, cor, data e militar.
- **12.2** Registra também as **alterações manuais**: overrides de dia vermelho (com a observação de controle), trocas/permutas, e pulos por impedimento.
- **12.3** O objetivo é ter rastro de **quem mexeu no quê e quando**.

---

## 13. Acesso e perfis de usuário

- **13.1 Consulta:** uma **tela aberta** na rede do quartel, **sem login**, onde todos veem as escalas e quando servem.
- **13.2 Gestão:** protegida por **login e senha**. Só gestores editam (cadastro, escalas, dispensas, previsão, overrides, trocas).
- **13.3** Pode haver **mais de um gestor**, cada um com seu próprio login — inclusive porque o próprio Sargento Brigada tira férias e dispensas.

---

## 14. Saída / exportação

- **14.1** O sistema exporta as escalas em **PDF simples**.
- **14.2** Não é necessário reproduzir a formatação de boletim, aditamento ou DIEx. A publicação no documento oficial é feita à parte, pela seção competente.

---

## 15. Escala de representação (Fase 2 — apenas registro)

- **15.1** A representação é uma **designação por evento**, não uma rotação. **Não** tem preta/vermelha e **não** gera folga.
- **15.2** Para cada evento (ex.: palestra), o gestor escala **N militares**. No evento seguinte, pode escalar outros.
- **15.3 Único vínculo com o serviço:** quem está de **serviço de 24h naquele dia não vai** à representação (está fisicamente ocupado). Ir à representação **não consome 48h** de ninguém.
- **15.4 Lotação por demanda do evento:** se pede 20 e há 20 disponíveis, vão todos; se pede 15, sobram 5; se falta efetivo, os disponíveis cobrem quantos eventos forem necessários. Um mesmo militar pode ir a vários eventos.
- **15.5** Em resumo, a representação é **quase como o militar cumprir o expediente normal do dia**.

---

## 16. Decisões assumidas (confira com atenção)

Estas foram acordadas ao longo da conversa e ficam destacadas por serem sutis:

- **16.1** O serviço de 24h **pertence ao dia em que começa** (08:00), e é esse dia que define preta ou vermelha.
- **16.2** A folga mínima é calculada a partir do **término** do serviço anterior. Com 48h, o militar fica disponível **no terceiro dia**; com 24h, **no dia seguinte ao término**.
- **16.3** A folga (rotação e piso mínimo) segue **quem estava escalado**, nunca quem fisicamente cobriu numa troca.
- **16.4** Isenção duradoura é feita por **não-participação**, não por um campo de "função".
- **16.5** Pode haver **múltiplos gestores** com login próprio; todo o restante do quartel apenas consulta.
- **16.6** A **escala é criada e excluída pelo gestor**; o sistema não traz escalas fixas. Cada escala carrega postos, participantes, cores, folga mínima e concorrentes próprios.
- **16.7** A folga mínima é **configurável por escala**, com **piso rígido de 24h**; 48h é apenas o default sugerido, não uma trava.
- **16.8** A **concorrência entre escalas é explícita e simétrica**, declarada pelo gestor. O piso aplicado ao entrar numa escala é o **da escala de destino**.
- **16.9** A **unidade de escalação é o militar-dia**: postos e quartos determinam quantos militares servem no dia, mas todos que servem no dia (mesmo um quarto) recebem a folga cheia.
- **16.10** A **janela do serviço (horário de início + duração)** é configurável por escala; o padrão 08:00/24h é só o default. A folga é sempre medida do **término** ao **início** do próximo, usando a janela real de cada escala.
- **16.11** O sistema **não é multitenant**: uma instalação por OM, mantida pela TI local.
- **16.12** A antiguidade das **praças de tropa** vem do **número de incorporação** informado no cadastro (regras 3.2.1 e 9.5).
- **16.13** A **concorrência é simétrica** (A concorre com B ⇔ B concorre com A) — garantido na gravação/serviço.
- **16.14** A permuta é **registro puro**, sem retribuição automática nem recálculo de folga; a folga segue quem estava escalado (regra 10.2/10.3).

---

## 17. Pontos ainda em aberto (para decidir antes de construir)

Nada aqui bloqueia a validação das regras acima, mas são escolhas que precisaremos fechar na etapa de desenho:

- **17.1** Formato exato do PDF de saída (uma escala por página? período? colunas? como listar vários postos por dia?).
- **17.2** Como o gestor registra a **migração inicial** das datas (importa da planilha atual ou digita?).
- **17.3** Até quando no futuro a previsão pode ser projetada (mês corrente? qualquer intervalo?).
- **17.4** Política de senha e recuperação de acesso dos gestores.
- **17.5 — RESOLVIDO.** O número de **postos** é **fixo por escala** (parâmetro de criação). Se uma OM precisa de quantidades diferentes, cria escalas diferentes. A flexibilidade que faltava era na **janela do serviço** (início + duração), que passou a ser configurável por escala (regras 2.4 e 4.2).
- **17.6 — RESOLVIDO.** O sistema **não é multitenant**: cada OM tem sua **própria instalação**, subida pela TI local. Não há separação por OM no banco nem visão multi-OM — cada instalação enxerga só a sua unidade.

---

*Fim do documento. Marque o que estiver errado, faltando ou impreciso — especialmente as seções 6, 7, 9 e 10, que são o núcleo da lógica.*
