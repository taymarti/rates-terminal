<#
.SYNOPSIS
  Start the rates terminal from the repo on the bundled runtime (runtime\python, runtime\jre): Deephaven with the app
  and the Python web server in one process (server\run.py, the same code as the wheel's `rates-terminal`). The browser
  opens on version C once the server is up (a minute or two the first time).

.EXAMPLE
  .\start.ps1                 Deephaven + the web server, in a window of its own; opens http://localhost:4070/
  .\start.ps1 -ServerOnly     Deephaven only (rates-terminal-server)
  .\start.ps1 -WebOnly        the web server only (rates-terminal-web): C runs on its in-page mock without a server
  .\start.ps1 -Dev            Deephaven, plus the front ends from source with hot reload (Vite's dev server: needs Node)
  .\start.ps1 -Hidden         no window (output in logs\); stop with .\stop.ps1
#>
param(
  [switch]$ServerOnly,
  [switch]$WebOnly,
  [switch]$Dev,
  [switch]$Hidden,
  [switch]$NoBrowser,
  [int]$ServerPort = 10050,
  [int]$WebPort = 4070,
  [string]$Heap = '6g'
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root 'runtime\python\python.exe'
$logs = Join-Path $root 'logs'
New-Item -ItemType Directory -Force $logs | Out-Null
if (-not (Test-Path $python)) { throw 'runtime\python is missing: see README.md, "Rebuilding the runtime".' }

function Test-Port([int]$port) {
  # Node listens on ::1 for "localhost", Java on 0.0.0.0: try both
  foreach ($addr in '127.0.0.1', '::1') {
    $c = New-Object Net.Sockets.TcpClient($(if ($addr -eq '::1') { 'InterNetworkV6' } else { 'InterNetwork' }))
    try { $c.Connect($addr, $port); return $true } catch { } finally { $c.Dispose() }
  }
  return $false
}

function Start-Part([string]$title, [string]$file, [string[]]$arguments, [string]$log) {
  if ($Hidden) {
    Start-Process -FilePath $file -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden `
      -RedirectStandardOutput (Join-Path $logs "$log.log") -RedirectStandardError (Join-Path $logs "$log.err.log") | Out-Null
  } else {
    # a console window of its own, titled, that stays open if the process stops (to read why)
    $cmd = "`$host.UI.RawUI.WindowTitle = '$title'; & '$file' $(($arguments | ForEach-Object { "'$_'" }) -join ' '); Write-Host ''; Read-Host '$title stopped. Enter to close'"
    Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile', '-Command', $cmd -WorkingDirectory $root | Out-Null
  }
}

$env:PYTHONUNBUFFERED = '1'
$py = @('-s', '-E', 'server\run.py')
$browser = if ($NoBrowser) { @('--no-browser') } else { @() }
foreach ($port in @($(if (-not $WebOnly) { $ServerPort }), $(if (-not $ServerOnly -and -not $Dev) { $WebPort }))) {
  if ($port -and (Test-Port $port)) { throw "Something already listens on :$port (the terminal running already? .\stop.ps1 stops it)." }
}

if ($WebOnly) {
  $open = if ($NoBrowser) { @() } else { @('--open') }
  Start-Part 'rates-terminal web' $python ($py + @('web', '--port', "$WebPort", '--dh-url', "http://127.0.0.1:$ServerPort") + $open) 'web'
} elseif ($ServerOnly -or $Dev) {
  Start-Part 'rates-terminal server' $python ($py + @('server', '--port', "$ServerPort", '--heap', $Heap)) 'server'
} else {
  Start-Part 'rates-terminal' $python ($py + @('--port', "$ServerPort", '--web-port', "$WebPort", '--heap', $Heap) + $browser) 'terminal'
}

if ($Dev) {
  $node = (Get-Command node -ErrorAction SilentlyContinue).Source
  if (-not $node) { throw '-Dev needs Node.js 20 or later (https://nodejs.org).' }
  $env:VITE_PROXY_URL = "http://localhost:$ServerPort"
  Start-Part 'rates-terminal dev' $node @((Join-Path $root 'node_modules\vite\bin\vite.js'), '--port', "$WebPort", '--strictPort') 'dev'
}

Write-Host ''
if (-not $ServerOnly) {
  Write-Host "  C  http://localhost:$WebPort/          React + table data (the in-page mock when Deephaven is down)"
  if (-not $WebOnly) {
    Write-Host "  B  http://localhost:$WebPort/b.html    React shell + server widgets"
    Write-Host "  A  http://localhost:$WebPort/iframe/widget/?name=terminal_a   deephaven.ui (opens in 20-40 s)"
  }
} else {
  Write-Host "  A  http://localhost:$ServerPort/iframe/widget/?name=terminal_a   deephaven.ui (opens in 20-40 s)"
}
if (-not $WebOnly) {
  Write-Host "     http://localhost:$ServerPort/ide/   the Deephaven IDE"
  Write-Host ''
  Write-Host 'The server takes a minute or two to start (it builds two years of market history).'
  if (-not $NoBrowser -and -not $ServerOnly -and -not $Dev) { Write-Host 'The browser opens on version C when it is up.' }
}
if ($Hidden) { Write-Host "Output: logs\. Stop with .\stop.ps1" }
