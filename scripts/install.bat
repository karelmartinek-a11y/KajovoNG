@echo off
setlocal enabledelayedexpansion

cd /d %~dp0\..

if not exist .venv (
  echo Creating venv...
  py -3 -m venv .venv
)

echo Activating venv...
call .venv\Scripts\activate.bat

echo Upgrading pip...
python -m pip install --upgrade pip

echo Installing requirements...
pip install -r requirements.txt

echo Done.
echo Optional: run scripts\fetch_fonts.ps1 to download Montserrat into resources\
