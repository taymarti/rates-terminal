<#
.SYNOPSIS
  Stop the rates terminal: whatever listens on the server's and the web app's ports.
#>
param([int]$ServerPort = 10050, [int]$WebPort = 4070)
foreach ($port in @($ServerPort, $WebPort)) {
  $owners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($id in $owners) {
    $p = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($p) {
      Write-Host "Stopping $($p.ProcessName) ($id) on :$port"
      Stop-Process -Id $id -Force -Confirm:$false
    }
  }
}
