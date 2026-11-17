@echo off
rem Abre a interface do Dual AI no navegador. Feche esta janela (ou Ctrl+C) para encerrar.
cd /d "%~dp0"
uv run --frozen dualai ui
if errorlevel 1 pause
