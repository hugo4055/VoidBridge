@echo off
chcp 65001 >nul
cd /d "%~dp0"

py -3.12 --version >nul 2>&1
if not errorlevel 1 (
    py -3.12 main.py
) else (
    py -3 main.py
)

if errorlevel 1 (
    echo.
    echo VoidBridge encerrou com erro. Leia a mensagem acima e o arquivo logs\voidbridge.log.
)
pause
