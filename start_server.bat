@echo off
echo ================================================================
echo         DEMARRAGE DU SERVEUR BACKEND
echo ================================================================
echo.

REM Activer l'environnement virtuel
echo [1/2] Activation environnement virtuel...
call venv\Scripts\activate.bat

REM Démarrer le serveur
echo.
echo [2/2] Demarrage du serveur...
echo.
echo ================================================================
echo   Serveur disponible sur: http://localhost:8000
echo   Documentation API: http://localhost:8000/docs
echo
echo   Pour arreter: Ctrl+C
echo ================================================================
echo.

python -m uvicorn app.main:app --reload --port 8000
