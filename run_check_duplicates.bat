@echo off
REM Script pour activer l'environnement virtuel et exécuter l'analyse des doublons

echo ========================================
echo Activation de l'environnement virtuel...
echo ========================================

REM Chercher l'environnement virtuel (venv ou .venv)
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
    echo Environnement virtuel active: venv
) else if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
    echo Environnement virtuel active: .venv
) else (
    echo ⚠️  Aucun environnement virtuel trouve (venv ou .venv)
    echo Continuation sans environnement virtuel...
)

echo.
echo ========================================
echo Execution de l'analyse des doublons...
echo ========================================
echo.

python check_duplicate_job_ids.py

echo.
echo ========================================
echo Analyse terminee
echo ========================================

pause
