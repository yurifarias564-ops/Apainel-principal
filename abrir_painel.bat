@echo off
title Painel CRM

echo ================================================
echo   PAINEL CRM - Iniciando...
echo ================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado!
    echo Instale em: https://python.org/downloads
    echo Marque "Add Python to PATH" na instalacao.
    pause
    exit /b
)

cd /d "%~dp0"

echo Abrindo painel em 3 segundos...
timeout /t 3 /nobreak >nul
start "" "http://localhost:8080"

echo.
echo ================================================
echo   Servidor rodando! NAO feche esta janela.
echo ================================================
echo.

python servidor_v4.py
pause
