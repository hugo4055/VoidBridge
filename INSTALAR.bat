@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ======================================
echo       VoidBridge - Instalador
echo ======================================
py -3.12 -m pip install --upgrade pip
if errorlevel 1 py -3 -m pip install --upgrade pip
py -3.12 -m pip install -r requirements.txt
if errorlevel 1 py -3 -m pip install -r requirements.txt
echo.
echo Instalacao finalizada. Configure o .env e config.json.
pause
