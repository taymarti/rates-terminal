<#
.SYNOPSIS
  Start the rates terminal: the Deephaven server (server\run.py on the bundled Python and Java) and the web app
  (the built dist\, served by Vite's preview with its proxy to the server), then open version C in the browser.

.EXAMPLE
  .\start.ps1                 server + web, each in its own window; opens http://localhost:4070/
  .\start.ps1 -Dev            the web app from source (Vite dev server, hot reload) instead of dist\
  .\start.ps1 -WebOnly        the web app only: version C runs on its in-page mock
  .\start.ps1 -ServerOnly     the Deephaven server only (version A: http://localhost:10050/iframe/widget/?name=terminal_a)
  .\start.ps1 -Hidden         no windows (logs in logs\); stop with .\stop.ps1
#>
param(
  [switch]$Dev,
  [switch]$WebOnly,
  [switch]$ServerOnly,
  [switch]$Hidden,
  [switch]$NoBrowser,
  [int]$ServerPort = 10050,
  [int]$WebPort = 4070,
  [string]$Heap = '6g'
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root 'runtime\python\python.exe'
$jre = Join-Path $root 'runtime\jre'
$logs = Join-Path $root 'logs'
New-Item -ItemType Directory -Force $logs | Out-Null

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

if (-not $WebOnly) {
  if (Test-Port $ServerPort) {
    Write-Host "Something already listens on :$ServerPort (the server running already?); not starting another."
  } else {
    if (-not (Test-Path $python)) { throw "runtime\python is missing: see README.md, 'Rebuilding the runtime'." }
    if (-not $env:JAVA_HOME -and (Test-Path $jre)) { $env:JAVA_HOME = $jre }
    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONNOUSERSITE = '1'
    Remove-Item (Join-Path $root 'server\data\ready.txt') -ErrorAction SilentlyContinue
    Write-Host "Starting the Deephaven server on :$ServerPort (Java: $env:JAVA_HOME)..."
    Start-Part 'rates-terminal server' $python @('-s', '-E', 'server\run.py', 'server', '--port', "$ServerPort", '--heap', $Heap) 'server'
  }
}

if (-not $ServerOnly) {
  $node = (Get-Command node -ErrorAction SilentlyContinue).Source
  if (-not $node) { throw 'Node.js is needed for the web app (https://nodejs.org, 20 or later).' }
  if (Test-Port $WebPort) {
    Write-Host "Something already listens on :$WebPort; not starting another web server."
  } else {
    $env:VITE_PROXY_URL = "http://localhost:$ServerPort"
    $env:VITE_DH_PORT = "$ServerPort"
    $vite = Join-Path $root 'node_modules\vite\bin\vite.js'
    $mode = if ($Dev) { @() } else { @('preview') }
    Write-Host "Starting the web app on :$WebPort ($(if ($Dev) { 'from source' } else { 'dist\' }))..."
    Start-Part 'rates-terminal web' $node (@($vite) + $mode + @('--port', "$WebPort", '--strictPort')) 'web'
  }
}

if (-not $WebOnly) {
  # the app script writes server\data\ready.txt when every table and chart_api are published (the port opens before)
  $ready = Join-Path $root 'server\data\ready.txt'
  if (-not (Test-Path $ready)) {
    Write-Host 'Waiting for the server (a minute or two: it builds two years of market history)...'
    $deadline = (Get-Date).AddMinutes(8)
    while (-not (Test-Path $ready)) {
      if ((Get-Date) -gt $deadline) { throw "The server did not come up on :$ServerPort; see its window or logs\server*.log." }
      Start-Sleep -Seconds 2
    }
  }
}

Write-Host ''
if (-not $ServerOnly) { Write-Host "  C  http://localhost:$WebPort/          (React + table data; the in-page mock when the server is down)" }
if (-not $ServerOnly -and -not $WebOnly) { Write-Host "  B  http://localhost:$WebPort/b.html    (React shell + server widgets)" }
if (-not $WebOnly) { Write-Host "  A  http://localhost:$ServerPort/iframe/widget/?name=terminal_a   (deephaven.ui; opens in 20-40 s)" }
if (-not $WebOnly) { Write-Host "     http://localhost:$ServerPort/ide/   (the Deephaven IDE)" }
Write-Host ''
if (-not $NoBrowser) {
  if ($ServerOnly) { Start-Process "http://localhost:$ServerPort/iframe/widget/?name=terminal_a" } else { Start-Process "http://localhost:$WebPort/" }
}
