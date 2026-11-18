# Arranque local NATIVO de SIGA (sin Docker): Core + 7 modulos + frontend Vite
# Alternativa recomendada: docker compose up -d
#
# Requisitos:
#   - PostgreSQL 18 local con la base indicada en .env (DB_NAME, por defecto siga_local)
#   - .env en la raiz con DB_PASSWORD y SECRET_KEY (copiar .env.example)
#   - venv:  py -3 -m venv siga_backend\venv ; siga_backend\venv\Scripts\python -m pip install -r siga_backend\requirements.txt
#   - Redis y NATS son opcionales: si no estan, el Core sigue sin cache/eventos.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "siga_backend"
$frontend = Join-Path $root "siga_frontend"
$python = Join-Path $backend "venv\Scripts\python.exe"
$envFile = Join-Path $root ".env"

if (-not (Test-Path $python)) {
    Write-Host "ERROR: no existe $python" -ForegroundColor Red
    Write-Host "Crealo con:  py -3 -m venv siga_backend\venv" -ForegroundColor Yellow
    Write-Host "Y luego:     siga_backend\venv\Scripts\python -m pip install -r siga_backend\requirements.txt" -ForegroundColor Yellow
    exit 1
}
if (-not (Test-Path $envFile)) {
    Write-Host "ERROR: falta $envFile (copia .env.example y completa DB_PASSWORD y SECRET_KEY)" -ForegroundColor Red
    exit 1
}

# Cargar .env sin sobrescribir variables ya definidas en la sesion
foreach ($line in Get-Content $envFile) {
    $l = $line.Trim()
    if (-not $l -or $l.StartsWith("#") -or -not $l.Contains("=")) { continue }
    $k, $v = $l.Split("=", 2)
    $k = $k.Trim(); $v = $v.Trim().Trim('"').Trim("'")
    if (-not (Test-Path "Env:$k")) { Set-Item -Path "Env:$k" -Value $v }
}

foreach ($req in "DB_PASSWORD", "SECRET_KEY") {
    if (-not (Get-Item "Env:$req" -ErrorAction SilentlyContinue).Value) {
        Write-Host "ERROR: $req no esta definido en .env" -ForegroundColor Red
        exit 1
    }
}

# En nativo no existe host.docker.internal: la BD esta en este mismo host
if ($env:DB_HOST -eq "host.docker.internal" -or -not $env:DB_HOST) { $env:DB_HOST = "localhost" }
if (-not $env:DB_NAME) { $env:DB_NAME = "siga_local" }
$env:SKIP_MIGRATIONS = "true"
$env:NATS_URL = "nats://localhost:4222"
$env:REDIS_URL = "redis://localhost:6379"
$env:VITE_PROXY_TARGET = "http://localhost:8000"

# Sin DNS de Docker: los manifests apuntan al nombre del servicio (mod-xxx).
# El core necesita saber que aqui todo vive en localhost.
$modulos = @(
    @{ n = "mod-usuarios";          p = 8001 },
    @{ n = "mod-planes-estudio";    p = 8002 },
    @{ n = "mod-programas-estudio"; p = 8005 },
    @{ n = "mod-gestion-academica"; p = 8006 },
    @{ n = "mod-auditoria";         p = 8007 },
    @{ n = "mod-evaluacion";        p = 8008 },
    @{ n = "mod-admision";          p = 8009 }
)

foreach ($m in $modulos) {
    $slug = $m.n
    if ($slug.StartsWith("mod-")) { $slug = $slug.Substring(4) }
    $clave = "SIGA_MODULE_" + $slug.Replace("-", "_").ToUpper() + "_URL"
    Set-Item -Path "Env:$clave" -Value "http://localhost:$($m.p)"
}

Write-Host " BD: $($env:DB_USER)@$($env:DB_HOST):$($env:DB_PORT)/$($env:DB_NAME)" -ForegroundColor DarkGray
Write-Host " Levantando Core en http://localhost:8000  (docs: /docs)" -ForegroundColor Cyan
Start-Process -FilePath $python -WorkingDirectory $backend -ArgumentList `
    "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--reload" `
    -WindowStyle Minimized

Start-Sleep -Seconds 2

foreach ($m in $modulos) {
    $dir = Join-Path $backend "modules\$($m.n)"
    if (-not (Test-Path $dir)) { Write-Host "  (falta) $($m.n)" -ForegroundColor Red; continue }
    Write-Host " Levantando $($m.n) en http://localhost:$($m.p)" -ForegroundColor Cyan
    Start-Process -FilePath $python -WorkingDirectory $dir -ArgumentList `
        "-m","uvicorn","main:app","--host","0.0.0.0","--port","$($m.p)","--reload" `
        -WindowStyle Minimized
    Start-Sleep -Milliseconds 700
}

Start-Sleep -Seconds 1
Write-Host " Levantando frontend (Vite)" -ForegroundColor Cyan
Start-Process -FilePath "npm.cmd" -WorkingDirectory $frontend -ArgumentList "run","dev" -WindowStyle Minimized

Write-Host ""
Write-Host " Todo lanzado. Espera ~10s y abre:" -ForegroundColor Green
Write-Host "   Frontend : http://localhost:5173"
Write-Host "   API docs : http://localhost:8000/docs"
Write-Host "   Credenciales de prueba: docs/90-CREDENCIALES-PRUEBA.md"
Write-Host ""
Write-Host " Para detener: stop_local.ps1" -ForegroundColor DarkGray
