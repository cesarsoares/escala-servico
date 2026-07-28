<#
.SINOPSE
    Faz o Sistema de Escala de Serviço subir sozinho com o Windows.

.DESCRIÇÃO
    Registra uma TAREFA AGENDADA que dispara "Ao iniciar o computador",
    rodando como SYSTEM. É de propósito, e não a pasta Inicializar nem um
    atalho: assim o sistema sobe ANTES de qualquer login. Numa OM a máquina
    reinicia por queda de energia e por atualização do Windows, e ninguém
    garante que alguém vai entrar na sessão logo em seguida - a consulta é
    aberta ao efetivo (regra 13.1) e não pode depender disso.

    Nada é baixado: Agendador de Tarefas e Firewall são nativos. A rede da OM
    pode não ter internet.

    Abre também a porta no Firewall, apenas nos perfis Particular e Domínio -
    a rede da OM. O perfil Público fica de fora.

.EXEMPLO
    # PowerShell COMO ADMINISTRADOR, na pasta do projeto:
    .\windows\instalar-servico.ps1

.EXEMPLO
    .\windows\instalar-servico.ps1 -Porta 8080 -SemFirewall
#>
[CmdletBinding()]
param(
    [int]$Porta = 8000,
    [string]$NomeTarefa = "EscalaServico",
    [switch]$SemFirewall
)

$ErrorActionPreference = "Stop"

function Falhar($mensagem) {
    Write-Host ""
    Write-Host "  $mensagem" -ForegroundColor Red
    Write-Host ""
    exit 1
}

# --- 1. administrador ---------------------------------------------------------
$identidade = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identidade)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Falhar "Abra o PowerShell COMO ADMINISTRADOR: registrar tarefa de sistema e abrir porta no firewall exigem isso."
}

# --- 2. onde está o projeto ---------------------------------------------------
$raiz = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $PSScriptRoot "escala.cmd"
if (-not (Test-Path $runner)) { Falhar "Não achei $runner." }

# A tarefa roda como SYSTEM, que NÃO enxerga pastas de perfil de usuário de
# forma confiável - OneDrive é o caso clássico, e é onde este projeto costuma
# viver na máquina de quem desenvolve. Numa OM, o lugar é C:\escala.
if ($raiz -match "OneDrive|\\Users\\") {
    Write-Host ""
    Write-Host "  ATENÇÃO: o projeto está em" -ForegroundColor Yellow
    Write-Host "    $raiz"
    Write-Host "  A tarefa roda como SYSTEM e pode não conseguir ler pastas de perfil"
    Write-Host "  de usuário (OneDrive em especial, que só existe depois do login)."
    Write-Host "  Recomendado: mover o projeto para C:\escala e rodar este script de lá."
    Write-Host ""
    $resposta = Read-Host "  Instalar assim mesmo? (s/N)"
    if ($resposta -ne "s") { exit 1 }
}

# --- 3. Python ----------------------------------------------------------------
$python = Join-Path $raiz ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $doPath = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $doPath) {
        Falhar "Python não encontrado. Instale o Python 3.12 e crie o ambiente: python -m venv .venv ; .venv\Scripts\pip install -r requirements.txt"
    }
    Write-Host "  Aviso: sem .venv no projeto - será usado o Python do PATH ($($doPath.Source))." -ForegroundColor Yellow
}

# --- 4. a tarefa --------------------------------------------------------------
if (Get-ScheduledTask -TaskName $NomeTarefa -ErrorAction SilentlyContinue) {
    Write-Host "  Tarefa '$NomeTarefa' já existe - será substituída."
    Unregister-ScheduledTask -TaskName $NomeTarefa -Confirm:$false
}

$acao = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c `"$runner`" $Porta" -WorkingDirectory $raiz
$gatilho = New-ScheduledTaskTrigger -AtStartup
$quem = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount `
    -RunLevel Highest

# ExecutionTimeLimit 0 = sem limite: é um servidor, não um lote que termina.
# RestartCount/Interval repõem o processo se ele morrer - o equivalente ao
# `restart: unless-stopped` do compose.
$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew -StartWhenAvailable

Register-ScheduledTask -TaskName $NomeTarefa -Action $acao -Trigger $gatilho `
    -Principal $quem -Settings $ajustes `
    -Description "Sistema de Escala de Serviço - sobe com o Windows (porta $Porta)." | Out-Null

# --- 5. firewall --------------------------------------------------------------
if (-not $SemFirewall) {
    $regra = "EscalaServico $Porta"
    Get-NetFirewallRule -DisplayName $regra -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $regra -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $Porta -Profile Domain,Private | Out-Null
    Write-Host "  Porta $Porta liberada no firewall (perfis Domínio e Particular)."
}

# --- 6. subir agora -----------------------------------------------------------
Start-ScheduledTask -TaskName $NomeTarefa

$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
       Select-Object -First 1).IPAddress

Write-Host ""
Write-Host "  Pronto. O sistema sobe sozinho a cada boot desta máquina." -ForegroundColor Green
Write-Host ""
Write-Host "    Nesta máquina:  http://localhost:$Porta"
if ($ip) { Write-Host "    Na rede da OM:  http://${ip}:$Porta" }
Write-Host "    Log:            $raiz\dados\log\escala.log"
Write-Host ""
Write-Host "  AGORA, ANTES DE DIVULGAR O ENDEREÇO: abra /gestao e crie o gestor."
Write-Host "  A senha de primeiro acesso está em:"
Write-Host "    $raiz\dados\primeiro-acesso.txt"
Write-Host "  (e no começo do log). Ela some sozinha quando o gestor é criado."
Write-Host ""
Write-Host "  Para remover:  .\windows\desinstalar-servico.ps1"
Write-Host ""
