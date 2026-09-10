# Healer — AI Self-Healing Incident Assistant
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Healer — AI Self-Healing Incident Assistant" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# Load .env
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match "^([^#=]+)=(.*)$") {
            [System.Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), "Process")
        }
    }
}

if (-not $env:ANTHROPIC_API_KEY) {
    Write-Host "ERROR: ANTHROPIC_API_KEY is not set." -ForegroundColor Red
    Write-Host "Copy .env.example to .env and add your key." -ForegroundColor Yellow
    exit 1
}

Write-Host "Installing dependencies..." -ForegroundColor Yellow
pip install -r requirements.txt --quiet

Write-Host ""
Write-Host "Starting Healer on http://localhost:8000" -ForegroundColor Green
Write-Host "Dashboard : http://localhost:8000" -ForegroundColor Green
Write-Host "API docs  : http://localhost:8000/docs" -ForegroundColor Green
Write-Host ""
Write-Host "Ensure the Spring Boot target is running on http://localhost:8080" -ForegroundColor Yellow
Write-Host ""

Set-Location backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
