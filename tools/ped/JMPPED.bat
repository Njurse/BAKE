@echo off
rem ============================================================
rem  JMPPED - pedestrian authoring launcher
rem
rem  Verbs (see tools/NAMING.md):
rem    extract | import | bake | preview | add-character |
rem    sync-source | migrate | status | rebuild-all
rem
rem  Examples:
rem    JMPPED.bat status
rem    JMPPED.bat extract --source DRIVER2\LEVELS\HAVANA.LEV
rem    JMPPED.bat import --folder out\characters\jerichod3
rem    JMPPED.bat bake --copy
rem    JMPPED.bat rebuild-all --source DRIVER2\LEVELS\HAVANA.LEV
rem ============================================================
setlocal EnableExtensions

set "HERE=%~dp0"
set "PY="

where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"

if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)

if not defined PY (
    echo JMPPED: Python 3 was not found - install it and put it on PATH.
    exit /b 3
)

%PY% "%HERE%jmpped.py" %*
exit /b %errorlevel%
