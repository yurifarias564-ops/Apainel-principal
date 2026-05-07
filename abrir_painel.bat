@echo off
title Painel CRM Kommo

echo ================================================
echo   PAINEL CRM - Iniciando...
echo ================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado!
    echo Instale em: https://python.org/downloads
    echo Marque "Add Python to PATH" na instalacao.
    echo.
    pause
    exit /b
)

cd /d "%~dp0"

if not exist "servidor_v3.py" (
    echo [ERRO] servidor_v3.py nao encontrado nesta pasta!
    echo Coloque todos os arquivos na mesma pasta.
    echo.
    pause
    exit /b
)

echo Abrindo painel no navegador em 3 segundos...
timeout /t 3 /nobreak >nul
start "" "http://localhost:8080"

echo.
echo ================================================
echo   Servidor rodando! NAO feche esta janela.
echo   Para parar: feche esta janela ou Ctrl+C
echo ================================================
echo.

python servidor_v3.py

pause
