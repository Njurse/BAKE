@echo off
rem ============================================================================
rem  BANK_WIZARD.bat  -  set up a new car bank, or manage an existing one
rem
rem  New bank: bank_new_basebank.py scaffolds a one-car bank from a donor D2
rem  car and writes its rebake / playtest / preview / wizard scripts.
rem  Manage: opens that bank's wizard.bat (export, import, bake, preview by slot,
rem  playtest, register city, refresh scripts).
rem ============================================================================
setlocal EnableDelayedExpansion
set "CAR=%~dp0"
set "CAR=%CAR:~0,-1%"
:menu
echo.
echo  ==== BAKE car bank wizard ====
echo    1  New bank from a donor car        (bank_new_basebank.py)
echo    2  Manage an existing bank          (its wizard.bat)
echo    3  Export a Driver 2 city's cars    (exportd2.py)
echo    0  Quit
choice /c 1230 /n /m "  select: "
set "C=%ERRORLEVEL%"
if "%C%"=="1" goto new
if "%C%"=="2" goto manage
if "%C%"=="3" goto export
exit /b 0
:new
set "NAME=" & set "CITY=CHICAGO" & set "MODEL=1" & set "SLOT=1" & set "OUT="
set /p "NAME=  bank name (e.g. FIREBIRD): "
if "!NAME!"=="" goto menu
set /p "CITY=  donor city CHICAGO/HAVANA/VEGAS/RIO [CHICAGO]: "
set /p "MODEL=  donor car model [1]: "
set /p "SLOT=  destination slot [1]: "
set /p "OUT=  bank folder (Enter = tools\car\out\basebank\!NAME!): "
set "OUTARG="
if not "!OUT!"=="" set OUTARG=--out "!OUT!"
python "%CAR%\bank_new_basebank.py" --name !NAME! --city !CITY! --model !MODEL! --slot !SLOT! !OUTARG! || goto menu
if "!OUT!"=="" set "OUT=%CAR%\out\basebank\!NAME!"
if exist "!OUT!\wizard.bat" call "!OUT!\wizard.bat"
goto menu
:manage
set "B="
set /p "B=  bank folder (the one with bank.json): "
if "!B!"=="" goto menu
if not exist "!B!\bank.json" (echo   no bank.json in !B! & goto menu)
if not exist "!B!\wizard.bat" python "%CAR%\bank_write_scripts.py" "!B!\bank.json"
call "!B!\wizard.bat"
goto menu
:export
set "L=" & set "O="
set /p "L=  Driver 2 .LEV to export: "
set /p "O=  output folder: "
if not "!L!"=="" if not "!O!"=="" python "%CAR%\exportd2.py" "!L!" -o "!O!"
goto menu
