param([int]$TimeoutSec = 60)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Pids = Join-Path $Root ".gax\pids"
$Logs = Join-Path $Root ".gax\logs"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$PidFile = Join-Path $Pids "worker.pid"
$Log = Join-Path $Logs "worker.log"
New-Item -ItemType Directory -Force $Pids, $Logs | Out-Null

function Read-From([string]$Path, [long]$Offset) {
    $fs = [System.IO.File]::Open($Path, "Open", "Read", "ReadWrite")
    try {
        $fs.Seek($Offset, "Begin") | Out-Null
        (New-Object System.IO.StreamReader($fs)).ReadToEnd()
    } finally { $fs.Dispose() }
}

if (Test-Path $PidFile) {
    $ids = @(Get-Content $PidFile)
    if ($ids | Where-Object { Get-Process -Id ([int]$_) -ErrorAction SilentlyContinue }) {
        Write-Host "worker already running pid=$($ids[-1])"
        exit 0
    }
    Remove-Item $PidFile
}
if (-not (Test-Path $Log)) { New-Item -ItemType File $Log | Out-Null }
$offset = (Get-Item $Log).Length
$p = Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"`"$Python`" -u -m gax.worker 1>>`"$Log`" 2>&1`"" `
    -WorkingDirectory $Root -WindowStyle Hidden -PassThru
Set-Content -Path $PidFile -Value $p.Id -Encoding ascii
$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ($true) {
    $m = [regex]::Match((Read-From $Log $offset), "worker pid=(\d+)")
    if ($m.Success) { break }
    if ($p.HasExited -or (Get-Date) -gt $deadline) {
        Write-Host (Read-From $Log $offset)
        throw "worker failed to start; see $Log"
    }
    Start-Sleep -Milliseconds 250
}
$realPid = $m.Groups[1].Value
Add-Content -Path $PidFile -Value $realPid -Encoding ascii
Write-Host "worker launcher pid=$($p.Id) real pid=$realPid log=$Log"
