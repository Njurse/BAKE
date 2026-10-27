@echo off
rem Console wizard for the JMPPED pedestrian authoring project.
rem Backed by jmpped.py; see tools/NAMING.md for the verbs.
setlocal
python "%~dp0jmpped_wizard.py" %*
if errorlevel 1 pause
