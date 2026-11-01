@echo off
rem ============================================================================
rem  wizard.bat  -  hub for every stage of the @NAME@ car bank
rem
rem  GENERATED from bake\tools\car\template_wizard.bat by bank_write_scripts.py.
rem  Each entry runs one stage; the scripts it calls sit beside this file.
rem ============================================================================
setlocal EnableDelayedExpansion
set "BANK=%~dp0"
set "BANK=%BANK:~0,-1%"
set "CAR=%BANK%\@TOOLS@"
set "EXEDIR=@EXEDIR@"
set "MIRROR=@MIRROR@"
:menu
echo.
echo  ==== @NAME@ car bank - wizard ====
echo    1  Export bank to editable files    (carbank_export.py -^> work\)
echo    2  Import an edited OBJ             (carbank_import.py -^> build\)
echo    3  Bake + install + register        (rebake.bat)
echo    4  Preview a car by slot, endless   (preview.bat)
echo    5  Playtest a car in a city         (playtest.bat, no bake)
echo    6  Register city only               (register_city.py -^> cities.ini)
echo    7  Refresh this bank's scripts      (bank_write_scripts.py --force)
echo    8  Open the bank folder
echo    0  Quit
choice /c 123456780 /n /m "  select: "
set "C=%ERRORLEVEL%"
if "%C%"=="1" python "%CAR%\carbank_export.py" "%BANK%\@NAME@.LEV" -o "%BANK%\work"
if "%C%"=="2" (
    set "OBJ="
    set /p "OBJ=  edited OBJ (e.g. work\cars\1_X\clean.obj): "
    if not "!OBJ!"=="" (
        if not exist "%BANK%\build" mkdir "%BANK%\build"
        for %%F in ("!OBJ!") do python "%CAR%\carbank_import.py" "!OBJ!" -o "%BANK%\build\%%~nF_geom.json"
    )
)
if "%C%"=="3" call "%BANK%\rebake.bat"
if "%C%"=="4" call "%BANK%\preview.bat"
if "%C%"=="5" call "%BANK%\playtest.bat"
if "%C%"=="6" (
    if "%EXEDIR%"=="" (echo   no game tree found for this bank) else python "%CAR%\register_city.py" @NAME@ "%MIRROR%\@NAME@.LEV" "%EXEDIR%"
)
if "%C%"=="7" python "%CAR%\bank_write_scripts.py" "%BANK%\bank.json" --force
if "%C%"=="8" start "" explorer "%BANK%"
if "%C%"=="9" exit /b 0
goto menu
