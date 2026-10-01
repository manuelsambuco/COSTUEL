@echo off
rem Doppio clic per fare il backup dei dati di OneMore (vedi backup.py)
cd /d "%~dp0"
if not exist "C:\venvs\onemore\Scripts\python.exe" (
    echo Ambiente Python non trovato in C:\venvs\onemore
    echo Crealo con:  python -m venv C:\venvs\onemore
    echo e poi:       C:\venvs\onemore\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
"C:\venvs\onemore\Scripts\python.exe" backup.py
