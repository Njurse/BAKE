@echo off
rem ============================================================================
rem  resolve_paths.bat - where the bake tools and the game tree are
rem
rem  Called by rebake.bat, playtest.bat, preview.bat and wizard.bat.
rem  BANK is the bank folder. CAR, EXEDIR and MIRROR may already hold the
rem  paths baked in when the scripts were written.
rem  Pass "game" as %1 to print the missing-game warning (rebake, playtest,
rem  wizard). preview.bat omits it: a preview does not need the game.
rem
rem  Order for both paths:
rem    (a) the baked-in path, when that folder exists
rem    (b) walk up from BANK
rem    (c) JERICHO_BAKE / JERICHO_GAME
rem    (d) a message naming the variable to set
rem  No setlocal: CAR, EXEDIR, MIRROR, CAR_MISSING and GAME_MISSING stick
rem  in the caller.
rem ============================================================================

set "CAR_MISSING="
set "GAME_MISSING="

rem ---- bake tools: bake\tools\car\make_write_scripts.py ----
if not "%CAR%"=="" if exist "%CAR%\make_write_scripts.py" goto car_ok
set "_WALK=%BANK%"
:car_walk
if exist "%_WALK%\bake\tools\car\make_write_scripts.py" set "CAR=%_WALK%\bake\tools\car"
if exist "%CAR%\make_write_scripts.py" goto car_ok
for %%I in ("%_WALK%\..") do set "_NEXT=%%~fI"
if "%_NEXT%"=="%_WALK%" goto car_env
set "_WALK=%_NEXT%"
goto car_walk
:car_env
if defined JERICHO_BAKE if exist "%JERICHO_BAKE%\make_write_scripts.py" set "CAR=%JERICHO_BAKE%"
if exist "%CAR%\make_write_scripts.py" goto car_ok
echo FAILED: bake tools not found.
echo   No make_write_scripts.py at the baked path:
echo     %CAR%
echo   Walked up from %BANK% looking for bake\tools\car\make_write_scripts.py.
echo   Set JERICHO_BAKE to the bake\tools\car folder and run this again.
set "CAR_MISSING=1"

:car_ok
rem ---- game: src_rebuild\bin\Release_dev (the folder with JERICHO_dev.exe) ----
rem A bank that already lives under Release_dev must resolve to THAT
rem Release_dev. Check "this directory is src_rebuild\bin\Release_dev" before
rem "this directory contains src_rebuild\bin\Release_dev", so a stray nested
rem copy (Release_dev\src_rebuild\bin\Release_dev) is not taken for the game.
rem A baked path that is that doubled copy is not "the game", even if mkdir
rem left the folder there.
set "_FULL="
if not "%EXEDIR%"=="" for %%I in ("%EXEDIR%") do set "_FULL=%%~fI"
if defined _FULL (
    echo %_FULL%| findstr /i /c:"\src_rebuild\bin\Release_dev\src_rebuild\bin\Release_dev" >nul
    if not errorlevel 1 set "EXEDIR="
)
if not "%EXEDIR%"=="" if exist "%EXEDIR%" goto game_mirror
set "EXEDIR="
set "_WALK=%BANK%"
:game_walk
for %%I in ("%_WALK%") do set "_LEAF=%%~nxI"
for %%I in ("%_WALK%\..") do set "_PAR=%%~nxI"
for %%I in ("%_WALK%\..\..") do set "_GPAR=%%~nxI"
if /i "%_LEAF%"=="Release_dev" if /i "%_PAR%"=="bin" if /i "%_GPAR%"=="src_rebuild" set "EXEDIR=%_WALK%"
if not "%EXEDIR%"=="" if exist "%EXEDIR%" goto game_mirror
if exist "%_WALK%\src_rebuild\bin\Release_dev" set "EXEDIR=%_WALK%\src_rebuild\bin\Release_dev"
if not "%EXEDIR%"=="" if exist "%EXEDIR%" goto game_mirror
for %%I in ("%_WALK%\..") do set "_NEXT=%%~fI"
if "%_NEXT%"=="%_WALK%" goto game_env
set "_WALK=%_NEXT%"
goto game_walk
:game_env
if defined JERICHO_GAME if exist "%JERICHO_GAME%" set "EXEDIR=%JERICHO_GAME%"
if not "%EXEDIR%"=="" if exist "%EXEDIR%" goto game_mirror
set "GAME_MISSING=1"
if /i "%~1"=="game" (
    echo warning: game tree not found. The bake still runs; install and city registration are skipped.
    echo   Looked for src_rebuild\bin\Release_dev above %BANK%.
    echo   Set JERICHO_GAME to the Release_dev folder that contains JERICHO_dev.exe.
)
goto game_done

:game_mirror
if exist "%MIRROR%" goto game_done
call set "_TAIL=%%BANK:%EXEDIR%\=%%"
if /i not "%_TAIL%"=="%BANK%" set "MIRROR=%BANK%"
if exist "%MIRROR%" goto game_done
for %%I in ("%BANK%") do set "MIRROR=%EXEDIR%\JERICHO\MODS\%%~nxI"

:game_done
set "_WALK="
set "_NEXT="
set "_LEAF="
set "_PAR="
set "_GPAR="
set "_TAIL="
