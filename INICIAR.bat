@echo off
cd /d "%~dp0"
py --version >nul 2>&1
if not errorlevel 1 (
 py server.py --open-browser
 pause
 exit /b
)
python --version >nul 2>&1
if not errorlevel 1 (
 python server.py --open-browser
 pause
 exit /b
)
echo Necesitas instalar Python 3.10 o superior.
echo Despues vuelve a abrir INICIAR.bat.
pause
