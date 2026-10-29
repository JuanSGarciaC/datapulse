<#
.SYNOPSIS
    Levanta DataPulse completo con Docker y abre el dashboard (demo de un solo comando).
.DESCRIPTION
    1. Crea .env con secretos aleatorios (si no existe). Nada de "change-me-*".
    2. docker compose up --build -d
    3. Copia los datos de ejemplo a data/landing y ejecuta el flujo diario.
    4. Abre el dashboard y muestra las URLs.
    Requisitos: Docker Desktop en ejecución. Para apagarlo: docker compose down
.EXAMPLE
    .\scripts\demo.ps1
#>
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

function Invoke-Step([string]$Exe, [string[]]$Arguments) {
    # Docker escribe el progreso por stderr; en PowerShell 5.1 con 'Stop' eso abortaría el script.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Exe @Arguments } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "Falló: $Exe $($Arguments -join ' ') (código $LASTEXITCODE)" }
}

function New-Secret {
    $bytes = New-Object byte[] 16
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker no está instalado o no está en el PATH. Instala Docker Desktop y vuelve a ejecutar.'
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop no está en ejecución. Ábrelo, espera a que arranque y reintenta.' }

# 1) .env con secretos aleatorios: cada marcador "change-me-*" recibe un valor propio, y el
#    mismo marcador siempre el mismo valor (la clave del dashboard es una de las claves de la API).
if (Test-Path .env) {
    Write-Host '.env ya existe: se conserva.'
}
else {
    $script:secrets = @{}
    $evaluator = [System.Text.RegularExpressions.MatchEvaluator] {
        param($m)
        if (-not $script:secrets.ContainsKey($m.Value)) { $script:secrets[$m.Value] = New-Secret }
        $script:secrets[$m.Value]
    }
    $text = [System.IO.File]::ReadAllText((Join-Path (Get-Location) '.env.example'))
    $text = [regex]::Replace($text, 'change-me-[A-Za-z0-9-]+', $evaluator)
    [System.IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), $text, (New-Object System.Text.UTF8Encoding $false))
    Write-Host '.env creado con secretos aleatorios (no se versiona).'
}

# 2) Servicios
Invoke-Step docker @('compose', 'up', '--build', '-d')

# 3) Datos de ejemplo y flujo diario (la copia se hace desde el host a propósito, ver docs/deployment.md)
New-Item -ItemType Directory -Force data\landing | Out-Null
Copy-Item -Recurse -Force data\samples\customers, data\samples\orders data\landing\

Write-Host 'Esperando a que el worker y Prefect estén listos...'
$ready = $false
for ($i = 0; $i -lt 60 -and -not $ready; $i++) {
    try {
        $ready = (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:4200/api/health' -TimeoutSec 3).StatusCode -eq 200
    }
    catch { Start-Sleep -Seconds 5 }
}
if (-not $ready) { throw 'Prefect no respondió a tiempo. Revisa: docker compose logs prefect-server' }

Invoke-Step docker @('compose', 'run', '--rm', 'worker', 'python', '-m', 'datapulse.flows', 'run', 'daily')

# 4) Dashboard
$ready = $false
for ($i = 0; $i -lt 30 -and -not $ready; $i++) {
    try {
        $ready = (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8501/_stcore/health' -TimeoutSec 3).Content -eq 'ok'
    }
    catch { Start-Sleep -Seconds 3 }
}
if (-not $ready) { throw 'El dashboard no respondió. Revisa: docker compose logs dashboard' }

Write-Host ''
Write-Host 'DataPulse está listo:' -ForegroundColor Green
Write-Host '  Dashboard   http://localhost:8501'
Write-Host '  API (docs)  http://localhost:8000/docs   (clave en .env: DATAPULSE_API_KEYS)'
Write-Host '  Prefect     http://localhost:4200'
Write-Host 'Para apagarlo: docker compose down'
Start-Process 'http://localhost:8501'
