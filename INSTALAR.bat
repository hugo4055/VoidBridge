@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ======================================
echo       VoidBridge - Instalador
echo ======================================

py -3.12 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py -3.12"
) else (
    set "PYTHON=py -3"
)

%PYTHON% -m pip install --upgrade pip
if errorlevel 1 goto erro
%PYTHON% -m pip install -r requirements.txt
if errorlevel 1 goto erro

echo.
echo Instalacao finalizada. Configure o .env e config.json.
pause
exit /b 0

:erro
echo.
echo ERRO: nao foi possivel instalar as dependencias.
echo Confira se o Python esta instalado e tente novamente.
pause
exit /b 1
