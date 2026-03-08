@echo off
echo ================================================================
echo         TEST DES NOUVEAUX ENDPOINTS CACHE
echo ================================================================
echo.

REM Activer l'environnement virtuel
echo [1/3] Activation environnement virtuel...
call venv\Scripts\activate.bat

REM Installer aiohttp si nécessaire
echo.
echo [2/3] Verification des dependances...
pip show aiohttp >nul 2>&1
if errorlevel 1 (
    echo Installation de aiohttp...
    pip install aiohttp
)

REM Lancer les tests
echo.
echo [3/3] Lancement des tests...
echo.
echo ================================================================
echo   ATTENTION: Le serveur doit etre demarre dans un autre terminal
echo   Commande: python -m uvicorn app.main:app --reload --port 8000
echo ================================================================
echo.
timeout /t 3 >nul

python test_cache_endpoints.py

echo.
echo ================================================================
echo                    TESTS TERMINES
echo ================================================================
echo.
echo Pour plus d'informations, consultez TEST_GUIDE.md
echo.
pause
