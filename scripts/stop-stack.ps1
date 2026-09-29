param([switch]$KeepMongo)
$Root = Split-Path -Parent $PSScriptRoot
$Pids = Join-Path $Root ".gax\pids"
$Container = "gae-atlas-local"

foreach ($name in "fleet-api", "temporal") {
    $file = Join-Path $Pids "$name.pid"
    if (-not (Test-Path $file)) { Write-Host "$name not tracked"; continue }
    foreach ($id in Get-Content $file) {
        if (Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue) {
            taskkill /PID $id /T /F 2>$null | Out-Null
            Write-Host "$name stopped pid=$id"
        }
    }
    Remove-Item $file
}

if ($KeepMongo) {
    Write-Host "$Container left running"
} else {
    docker stop -t 60 $Container | Out-Null
    Write-Host "$Container stopped"
}
