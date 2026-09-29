$Root = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $Root ".gax\pids\worker.pid"
if (-not (Test-Path $PidFile)) { Write-Host "worker not tracked"; exit 0 }
$ids = @(Get-Content $PidFile)
[array]::Reverse($ids)
foreach ($id in $ids) {
    if (Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue) {
        taskkill /PID $id /T /F 2>$null | Out-Null
        Write-Host "worker stopped pid=$id"
    }
}
Remove-Item $PidFile
