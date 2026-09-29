param([int]$FleetPort = 8081)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$State = Join-Path $Root ".gax"
$Pids = Join-Path $State "pids"
$Logs = Join-Path $State "logs"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Temporal = if ($env:TEMPORAL_CLI) { $env:TEMPORAL_CLI } else { "C:\Users\bhara\tools\temporal\temporal.exe" }
$Container = "gae-atlas-local"
New-Item -ItemType Directory -Force $Pids, $Logs | Out-Null

function Wait-Until([string]$Name, [scriptblock]$Check, [int]$TimeoutSec = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while (-not (& $Check)) {
        if ((Get-Date) -gt $deadline) { throw "$Name not ready after $TimeoutSec s" }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "$Name ready"
}

function Get-TrackedProcess([string]$Name) {
    $file = Join-Path $Pids "$Name.pid"
    if (-not (Test-Path $file)) { return $null }
    Get-Process -Id ([int](Get-Content $file -TotalCount 1)) -ErrorAction SilentlyContinue
}

function Test-Port([int]$Port) {
    [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Test-FleetHealthy {
    try { (Invoke-RestMethod "http://127.0.0.1:$FleetPort/healthz" -TimeoutSec 2).status -eq "ok" } catch { $false }
}

$exists = docker ps -a --filter "name=^$Container$" --format "{{.Names}}"
if (-not $exists) {
    docker run -d --name $Container -p 27017:27017 -v gae-atlas-data:/data/db mongodb/mongodb-atlas-local:preview | Out-Null
} else {
    docker start $Container | Out-Null
}
Wait-Until "atlas-local" { (docker inspect -f "{{.State.Health.Status}}" $Container) -eq "healthy" } 180

if (Get-TrackedProcess "temporal") {
    Write-Host "temporal already running"
} else {
    if (Test-Port 7233) { throw "port 7233 is in use by an untracked process" }
    $p = Start-Process -FilePath $Temporal -ArgumentList "server", "start-dev", "--db-filename", (Join-Path $State "temporal.db") `
        -RedirectStandardOutput (Join-Path $Logs "temporal.out.log") -RedirectStandardError (Join-Path $Logs "temporal.err.log") `
        -WindowStyle Hidden -PassThru
    Set-Content -Path (Join-Path $Pids "temporal.pid") -Value $p.Id -Encoding ascii
    Write-Host "temporal pid=$($p.Id)"
}
Wait-Until "temporal (7233)" { Test-Port 7233 } 60

if (Get-TrackedProcess "fleet-api") {
    Write-Host "fleet-api already running"
} else {
    if (Test-Port $FleetPort) { throw "port $FleetPort is in use by an untracked process" }
    $out = Join-Path $Logs "fleet-api.out.log"
    $err = Join-Path $Logs "fleet-api.err.log"
    $p = Start-Process -FilePath $Python -ArgumentList "-m", "uvicorn", "fleet_api.app:create_app", "--factory", "--host", "127.0.0.1", "--port", $FleetPort `
        -WorkingDirectory $Root -RedirectStandardOutput $out -RedirectStandardError $err -WindowStyle Hidden -PassThru
    Set-Content -Path (Join-Path $Pids "fleet-api.pid") -Value $p.Id -Encoding ascii
    $deadline = (Get-Date).AddSeconds(30)
    while (-not (Test-FleetHealthy)) {
        if ($p.HasExited -or (Get-Date) -gt $deadline) {
            Get-Content $err -Tail 15
            throw "fleet-api failed to start; see $err"
        }
        Start-Sleep -Milliseconds 500
    }
    $realPid = (Invoke-RestMethod "http://127.0.0.1:$FleetPort/healthz").pid
    Add-Content -Path (Join-Path $Pids "fleet-api.pid") -Value $realPid -Encoding ascii
    Write-Host "fleet-api launcher pid=$($p.Id) real pid=$realPid"
}
Wait-Until "fleet-api ($FleetPort)" { Test-FleetHealthy } 30
Write-Host "stack up. Temporal UI http://localhost:8233  fleet-api http://127.0.0.1:$FleetPort (LOCAL-ONLY auth)"
