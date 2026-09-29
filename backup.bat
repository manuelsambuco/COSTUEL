@echo off
rem Doppio clic per fare il backup dei dati di COSTUEL (vedi backup.py)
cd /d "%~dp0"
if not exist "C:\venvs\costuel\Scripts\python.exe" (
    echo Ambiente Python non trovato in C:\venvs\costuel
    echo Crealo con:  python -m venv C:\venvs\costuel
    echo e poi:       C:\venvs\costuel\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
"C:\venvs\costuel\Scripts\python.exe" backup.py
