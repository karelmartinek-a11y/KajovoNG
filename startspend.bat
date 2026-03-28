@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "PYTHON_EXE=%ROOT_DIR%.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo Chyba: Nenalezeno virtualni prostredi v "%ROOT_DIR%.venv".
    echo Vytvorte nebo obnovte .venv a zkuste to znovu.
    exit /b 1
)

pushd "%ROOT_DIR%" >nul
"%PYTHON_EXE%" -m kajovospend
set "EXIT_CODE=%ERRORLEVEL%"
popd >nul

exit /b %EXIT_CODE%
