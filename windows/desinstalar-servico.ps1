<#
.SINOPSE
    Remove a tarefa agendada e a regra de firewall criadas por
    instalar-servico.ps1.

    NÃO apaga o banco nem a pasta dados\ - o histórico da escala fica.
    Para levar os dados embora, copie dados\ antes (ver o manual, 2.7).
#>
[CmdletBinding()]
param(
    [int]$Porta = 8000,
    [string]$NomeTarefa = "EscalaServico"
)

$ErrorActionPreference = "Stop"

$identidade = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identidade)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host ""
    Write-Host "  Abra o PowerShell COMO ADMINISTRADOR." -ForegroundColor Red
    Write-Host ""
    exit 1
}

if (Get-ScheduledTask -TaskName $NomeTarefa -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $NomeTarefa -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $NomeTarefa -Confirm:$false
    Write-Host "  Tarefa '$NomeTarefa' removida."
} else {
    Write-Host "  Tarefa '$NomeTarefa' não estava registrada."
}

$regra = "EscalaServico $Porta"
$achada = Get-NetFirewallRule -DisplayName $regra -ErrorAction SilentlyContinue
if ($achada) {
    $achada | Remove-NetFirewallRule
    Write-Host "  Regra de firewall '$regra' removida."
}

Write-Host ""
Write-Host "  O banco continua em dados\escala.sqlite3 - nada foi apagado." -ForegroundColor Green
Write-Host ""
