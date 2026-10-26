# Levanta el bot. Usa el .env de %LOCALAPPDATA%\FinancialAgent si existe; si no, el del proyecto.
$envFile = Join-Path $env:LOCALAPPDATA "FinancialAgent\.env"
if (-not (Test-Path $envFile)) {
    $envFile = Join-Path $PSScriptRoot ".env"
}
# uv borra las barras invertidas de la ruta de --env-file: se pasa con barras normales.
$envFile = $envFile -replace '\\', '/'

# Las rutas relativas del .env (por ejemplo data/...) se resuelven desde la carpeta del proyecto.
Push-Location $PSScriptRoot
try {
    uv run --env-file $envFile uvicorn financial_agent.app:create_app --factory --host 127.0.0.1 --port 8000
}
finally {
    Pop-Location
}
