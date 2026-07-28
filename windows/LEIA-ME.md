# Instalar no Windows (sem Docker)

Para a máquina que fica ligada na seção e serve a escala ao efetivo. O sistema
sobe **sozinho a cada boot**, antes de qualquer login.

> **Por que não Docker aqui?** No Windows o Docker depende do WSL, e o container
> cai quando a sessão do WSL encerra — medido nesta máquina, `Exited (0)` cerca
> de um minuto depois de subir. No servidor Linux da OM o Docker é o caminho
> certo; numa estação Windows, este aqui é.

## Antes de começar

1. **Ponha o projeto em `C:\escala`.** Não deixe em `Documentos` nem no
   OneDrive: o sistema roda como SYSTEM, que não enxerga pasta de perfil de
   usuário de forma confiável — e o OneDrive só existe depois do login.
2. **Instale o Python 3.12** (python.org). Marque *Add python.exe to PATH*.
3. Prepare o ambiente, num PowerShell comum dentro de `C:\escala`:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

> Se o `weasyprint` reclamar, pode ignorar: nada no caminho de uso o utiliza
> hoje — a impressão da escala sai pelo navegador. Instale o resto com
> `.venv\Scripts\pip install -r requirements.txt --no-deps` apenas se
> necessário, ou remova a linha do `requirements.txt`.

## Instalar

PowerShell **como Administrador**, em `C:\escala`:

```powershell
.\windows\instalar-servico.ps1
```

O script:

- registra a tarefa agendada **EscalaServico**, disparada *Ao iniciar o
  computador*, rodando como SYSTEM e reiniciando sozinha se o processo cair;
- libera a **porta 8000** no firewall, só nos perfis *Domínio* e *Particular*
  (a rede da OM). O perfil *Público* fica de fora;
- sobe o sistema na hora e mostra o endereço para a rede.

Outra porta: `.\windows\instalar-servico.ps1 -Porta 8080`.
Sem mexer no firewall: acrescente `-SemFirewall`.

## O passo que não pode ser pulado

Assim que instalar, **abra `/gestao` e crie o gestor** — antes de divulgar o
endereço. Enquanto não existe gestor, a tela de primeiro acesso fica aberta a
quem alcança a porta.

Ela pede a **senha de instalação**, que está em:

```
C:\escala\dados\primeiro-acesso.txt
```

e também aparece no começo do log. A senha some sozinha quando o gestor é
criado. Perdeu antes disso? Apague o arquivo e recarregue a página: nasce outra.

## No dia a dia

| O quê | Como |
|---|---|
| Ver o log | `C:\escala\dados\log\escala.log` |
| Parar | `Stop-ScheduledTask -TaskName EscalaServico` |
| Iniciar | `Start-ScheduledTask -TaskName EscalaServico` |
| Ver o estado | `Get-ScheduledTask -TaskName EscalaServico \| Get-ScheduledTaskInfo` |
| Remover | `.\windows\desinstalar-servico.ps1` (não apaga o banco) |
| Testar à mão | `.\windows\escala.cmd` (roda em primeiro plano) |

**Atualizar o sistema:** pare a tarefa, atualize os arquivos (`git pull` ou
cópia da pasta, preservando `dados\`), rode
`.venv\Scripts\pip install -r requirements.txt` e inicie de novo. As migrações
do banco rodam sozinhas no arranque.

**Backup:** o sistema guarda uma cópia por dia em `C:\escala\dados\backups`
(últimas 7) e o gestor baixa o backup completo em *Configurações → Backup e
restauração*. Trocar de máquina: pare a tarefa, copie a pasta `dados\` inteira
e instale do outro lado. Detalhe no manual, seção *2.7 Trocar de máquina*.
