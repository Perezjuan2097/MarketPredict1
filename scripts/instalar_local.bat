@echo off
REM Instalación local en Windows: crea el entorno virtual, instala todo y verifica.
python -m venv .venv || goto :error
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-dev.txt || goto :error
python scripts\verify_install.py
goto :eof
:error
echo Fallo la instalacion. Copie el mensaje de error y compartalo.
exit /b 1
