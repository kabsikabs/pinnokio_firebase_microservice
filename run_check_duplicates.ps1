# Script PowerShell pour activer l'environnement virtuel et exécuter l'analyse des doublons

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Activation de l'environnement virtuel..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Chercher l'environnement virtuel (venv ou .venv)
if (Test-Path "venv\Scripts\Activate.ps1") {
    & "venv\Scripts\Activate.ps1"
    Write-Host "Environnement virtuel activé: venv" -ForegroundColor Green
} elseif (Test-Path ".venv\Scripts\Activate.ps1") {
    & ".venv\Scripts\Activate.ps1"
    Write-Host "Environnement virtuel activé: .venv" -ForegroundColor Green
} else {
    Write-Host "⚠️  Aucun environnement virtuel trouvé (venv ou .venv)" -ForegroundColor Yellow
    Write-Host "Continuation sans environnement virtuel..." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Exécution de l'analyse des doublons..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

python check_duplicate_job_ids.py

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Analyse terminée" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

Read-Host "Appuyez sur Entrée pour continuer"
