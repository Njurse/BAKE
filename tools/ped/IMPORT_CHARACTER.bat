@echo off
rem Drag a character folder onto this file.
rem The folder must contain character.json, model.obj and textures\.
setlocal
if "%~1"=="" (
  echo Drag a character folder onto this file.
  echo It must contain character.json, model.obj and textures\.
  pause
  exit /b 1
)
python "%~dp0jmpped.py" import --folder "%~1"
set RC=%ERRORLEVEL%
if not "%RC%"=="0" pause
exit /b %RC%
