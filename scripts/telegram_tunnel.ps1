<#
  Обратный SSH-туннель: сервер ходит в Telegram через этот компьютер (SOCKS5 на 127.0.0.1:1080 сервера, наружу не открыт).
  Нужен, пока api.telegram.org недоступен с хостинга. Окно должно оставаться открытым; при обрыве туннель поднимается заново.

  Пример: .\scripts\telegram_tunnel.ps1 -Key "$env:USERPROFILE\Downloads\privatekey.pem" -Server ubuntu@195.209.220.227
  На сервере нужна надстройка docker-compose.telegram-tunnel.yml (docs/knowledge-base/08-dev-runbook.md, «Telegram через туннель»).
#>
param(
    [Parameter(Mandatory = $true)][string]$Key,
    [Parameter(Mandatory = $true)][string]$Server,
    [string]$Bind = "127.0.0.1",
    [int]$Port = 1080
)
while ($true) {
    Write-Host "[$(Get-Date -Format T)] туннель -> $Server ($Bind`:$Port)"
    ssh -i $Key -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -R "${Bind}:${Port}" $Server
    Write-Host "[$(Get-Date -Format T)] туннель оборван, повтор через 5 с (Ctrl+C - остановить)"
    Start-Sleep -Seconds 5
}
