@echo off
rem ============================================================================
rem  preview.bat  -  endless preview of one @NAME@ car, picked by SLOT (no bake)
rem
rem  GENERATED from bake\tools\car\template_preview.bat by make_write_scripts.py.
rem  Lists the bank's slots, asks which one to load, then opens preview_car.py
rem  with no timeout: it spins until you press Esc / Q or close the window, then
rem  asks again (Enter quits). Previews the authoring art - run rebake.bat to
rem  put changes in the game.
rem ============================================================================
setlocal EnableDelayedExpansion
set "BANK=%~dp0"
set "BANK=%BANK:~0,-1%"
set "CAR=@TOOLS@"
if not exist "%CAR%\preview_car.py" (echo FAILED: no preview_car.py at %CAR% & exit /b 1)
:pick
set /a N=0
echo.
echo  ==== @NAME@ preview - which slot? ====
for /f "usebackq tokens=1,2" %%A in (`python -c "import json;[print(o['slot'],o['car']) for o in json.load(open(r'%BANK%\bank.json'))['declare']['order']]"`) do (
    set /a N+=1
    set "SLOT!N!=%%A"
    set "ID!N!=%%B"
    echo     slot %%A  %%B
)
set "PICK="
set /p "PICK=  slot to preview (Enter = quit): "
if "!PICK!"=="" exit /b 0
set "PCAR="
for /l %%I in (1,1,!N!) do if "!SLOT%%I!"=="!PICK!" set "PCAR=!ID%%I!"
if "!PCAR!"=="" (echo   no car in slot !PICK! & goto pick)
echo   previewing !PCAR! ^(slot !PICK!^) - Esc / Q or close the window to quit
python "%CAR%\preview_car.py" "%BANK%\bank.json" --car !PCAR! --timeout 0 @PREVIEW@
goto pick
