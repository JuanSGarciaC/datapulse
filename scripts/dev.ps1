<#
.SYNOPSIS
    Comandos de desarrollo de DataPulse para Windows (PowerShell 5.1+ o 7+).
.EXAMPLE
    .\scripts\dev.ps1 setup
    .\scripts\dev.ps1 up
    .\scripts\dev.ps1 test
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('help', 'setup', 'fmt', 'lint', 'typecheck', 'test', 'test-unit', 'test-live',
                 'security', 'check', 'migrate', 'generate', 'mock', 'api', 'dashboard', 'pipeline', 'ingest-samples', 'dbt-build', 'dbt-docs', 'up', 'down', 'logs', 'createtestdb')]
    [string]$Command = 'help'
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

function Import-DotEnv {
    # Carga .env en el proceso actual (las pruebas y Alembic leen variables de entorno).
    if (-not (Test-Path '.env')) {
        throw "No existe .env. Ejecuta: Copy-Item .env.example .env  (y cambia las contraseñas)"
    }
    Get-Content '.env' | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
            $name, $value = $line.Split('=', 2)
            $value = ($value -replace '\s+#.*$', '').Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($name.Trim(), $value, 'Process')
        }
    }
}

function Invoke-Step([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Falló: $Exe $($Arguments -join ' ') (código $LASTEXITCODE)" }
}

switch ($Command) {
    'help' {
        Get-Help $PSCommandPath -Detailed | Out-Host
        'Comandos: setup, fmt, lint, typecheck, test, test-unit, test-live, security, check, migrate, generate, mock, api, dashboard, pipeline, ingest-samples, dbt-build, dbt-docs, up, down, logs, createtestdb'
    }
    'setup'     { Invoke-Step uv @('sync', '--frozen') }
    'fmt'       { Invoke-Step uv @('run', 'ruff', 'format', '.'); Invoke-Step uv @('run', 'ruff', 'check', '.', '--fix') }
    'lint'      { Invoke-Step uv @('run', 'ruff', 'format', '--check', '.'); Invoke-Step uv @('run', 'ruff', 'check', '.') }
    'typecheck' { Invoke-Step uv @('run', 'mypy') }
    'test-unit' { Invoke-Step uv @('run', 'pytest', 'tests/unit') }
    'test'      { Import-DotEnv; Invoke-Step uv @('run', 'pytest', '--cov') }
    'test-live' { Invoke-Step uv @('run', 'pytest', 'tests/contract', '-m', 'live', '-v') }
    'generate'  { Invoke-Step uv @('run', 'python', '-m', 'datapulse.synthetic') }
    'mock'      { Invoke-Step uv @('run', 'python', '-m', 'datapulse.mock_source') }
    'api'       {
        # La API se conecta con su usuario de solo lectura, nunca con el administrador.
        Import-DotEnv
        $env:DATAPULSE_DB_USER = 'datapulse_api'
        $env:DATAPULSE_DB_PASSWORD = $env:DATAPULSE_API_DB_PASSWORD
        Invoke-Step uv @('run', 'python', '-m', 'datapulse.api')
    }
    'dashboard' { Import-DotEnv; Invoke-Step uv @('run', 'python', '-m', 'datapulse.dashboard') }
    'pipeline'  {
        Import-DotEnv
        Invoke-Step uv @('run', 'python', '-m', 'datapulse.flows', 'seed-landing')
        Invoke-Step uv @('run', 'python', '-m', 'datapulse.flows', 'run', 'daily')
    }
    'dbt-build' { Import-DotEnv; Invoke-Step uv @('run', 'dbt', 'build', '--project-dir', 'dbt', '--profiles-dir', 'dbt') }
    'dbt-docs'  {
        Import-DotEnv
        Invoke-Step uv @('run', 'dbt', 'docs', 'generate', '--project-dir', 'dbt', '--profiles-dir', 'dbt')
        Invoke-Step uv @('run', 'dbt', 'docs', 'serve', '--project-dir', 'dbt', '--profiles-dir', 'dbt', '--port', '8088')
    }
    'ingest-samples' {
        Import-DotEnv
        $customers = (Get-ChildItem data\samples\customers\*.csv | Sort-Object Name).FullName
        $orders = (Get-ChildItem data\samples\orders\*.json | Sort-Object Name).FullName
        Invoke-Step uv (@('run', 'python', '-m', 'datapulse.ingestion', 'customers') + $customers)
        Invoke-Step uv (@('run', 'python', '-m', 'datapulse.ingestion', 'orders') + $orders)
        Invoke-Step uv @('run', 'python', '-m', 'datapulse.ingestion', 'products')
    }
    'security'  {
        Invoke-Step uv @('run', 'bandit', '-q', '-c', 'pyproject.toml', '-r', 'src')
        $req = Join-Path $env:TEMP 'datapulse-audit-req.txt'
        uv export --frozen --no-dev --no-hashes --no-emit-project --format requirements-txt | Out-File -Encoding utf8 $req
        Invoke-Step uv @('run', 'pip-audit', '-r', $req, '--progress-spinner', 'off')
    }
    'check'     {
        & $PSCommandPath lint; & $PSCommandPath typecheck; & $PSCommandPath test; & $PSCommandPath security
        Invoke-Step uv @('run', 'python', '-m', 'datapulse.synthetic', '--check')
    }
    'migrate'   { Import-DotEnv; Invoke-Step uv @('run', 'alembic', 'upgrade', 'head'); Invoke-Step uv @('run', 'python', '-m', 'datapulse.db.bootstrap') }
    'up'        { Invoke-Step docker @('compose', 'up', '--build', '-d') }
    'down'      { Invoke-Step docker @('compose', 'down') }
    'logs'      { docker compose logs -f }
    'createtestdb' {
        # Crea la base de pruebas dentro del contenedor de Postgres (una sola vez).
        Import-DotEnv
        Invoke-Step docker @('compose', 'exec', '-T', 'postgres', 'psql', '-U', $env:DATAPULSE_DB_USER,
            '-d', $env:DATAPULSE_DB_NAME, '-c', "CREATE DATABASE $($env:DATAPULSE_TEST_DB_NAME)")
    }
}
