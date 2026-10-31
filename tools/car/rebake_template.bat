@echo off
rem ============================================================================
rem  rebake.bat [plain^|flipy^|full^|winding^|normals^|flipfull]  -  @NAME@ bank
rem
rem  GENERATED from bake\tools\car\rebake_template.bat by rebake_install.py
rem  (exportd2 writes it on export). Edit the template, then re-run:
rem    python bake\tools\car\rebake_install.py <bank folder>\bank.json --force
rem
rem  @NAME@ is a CAR BANK, not a level: Take a Ride runs in a real city
rem  (-level chicago/havana/lasvegas/rio) and carhacks.ini's
rem  "import = 5:<bank city>:<model>" pulls the picked car from this bank.
rem  Bakes bank.json into build\, installs next to bank.json and into the game
rem  mirror (src_rebuild\bin\Release_dev\...), registers @NAME@ in
rem  JERICHO\CONFIG\cities.ini, then previews / launches one car.
rem ============================================================================
setlocal EnableDelayedExpansion
set "MODE=%~1"
if "%MODE%"=="" set "MODE=@MODE@"
set "FLIP="
if /i "%MODE%"=="flipy"    set "FLIP=--flip-y"
if /i "%MODE%"=="full"     set "FLIP=--flip-y --flip-z"
if /i "%MODE%"=="winding"  set "FLIP=--flip-winding"
if /i "%MODE%"=="normals"  set "FLIP=--flip-normals"
if /i "%MODE%"=="flipfull" set "FLIP=--flip-y --flip-winding --flip-normals"
set "BANK=%~dp0"
set "BANK=%BANK:~0,-1%"
set "CAR=%BANK%\@TOOLS@"
set "OUT=%BANK%\build"
set "LOG=%OUT%\rebake.log"
set "EXEDIR=@EXEDIR@"
set "MIRROR=@MIRROR@"
if not exist "%CAR%\carbank_bake.py" (echo FAILED: no bake tools at %CAR% & exit /b 1)
if not exist "%OUT%" mkdir "%OUT%"
if exist "%OUT%\@NAME@.LEV" del /q "%OUT%\@NAME@.LEV"
if exist "%OUT%\@NAME@.LCF" del /q "%OUT%\@NAME@.LCF"
echo.
echo  ==== @NAME@ rebake [%MODE%] ====
python "%CAR%\carbank_bake.py" "%BANK%\bank.json" -o "%OUT%" @ARGS@ %FLIP% > "%LOG%" 2>&1
if errorlevel 1 goto failed
if not exist "%OUT%\@NAME@.LEV" goto failed
findstr /i "warning" "%LOG%"
for /f "usebackq delims=" %%L in (`findstr /i /c:"bank ok" "%LOG%"`) do echo   %%L
copy /Y "%OUT%\@NAME@.LEV" "%BANK%\" >nul
copy /Y "%OUT%\@NAME@.LCF" "%BANK%\" >nul
if not "%MIRROR%"=="" (
    if not exist "%MIRROR%" mkdir "%MIRROR%"
    copy /Y "%OUT%\@NAME@.LEV" "%MIRROR%\" >nul
    copy /Y "%OUT%\@NAME@.LCF" "%MIRROR%\" >nul
    echo   installed to %MIRROR%
)
if "%EXEDIR%"=="" exit /b 0
python "%CAR%\register_city.py" @NAME@ "%MIRROR%\@NAME@.LEV" "%EXEDIR%"
rem ---- pick the car for the test/preview (bank.json order) ----
set /a N=0
for /f "usebackq tokens=1,2" %%A in (`python -c "import json;[print(o['slot'],o['car']) for o in json.load(open(r'%BANK%\bank.json'))['declare']['order']]"`) do (
    set /a N+=1
    set "SLOT!N!=%%A"
    set "ID!N!=%%B"
    echo     !N!  %%B  ^(slot %%A^)
)
set "PICK=1"
if !N! GTR 1 set /p "PICK=  car for test/preview [1]: "
if "!PICK!"=="" set "PICK=1"
set "PCAR=!ID%PICK%!"
set "PSLOT=!SLOT%PICK%!"
if "!PCAR!"=="" (set "PCAR=!ID1!" & set "PSLOT=!SLOT1!")
echo   test car: !PCAR! ^(bank slot !PSLOT!^)
set "STOPF=%OUT%\preview.stop"
if exist "%STOPF%" del /q "%STOPF%"
if not "%NOPREVIEW%"=="1" if exist "%CAR%\preview_car.py" start "" /b python "%CAR%\preview_car.py" "%BANK%\bank.json" --car !PCAR! --timeout 12 --stop-file "%STOPF%" @PREVIEW@
echo.
echo   Take a Ride with !PCAR! - in which city?  (@NAME@ is a car bank, not a level)
echo     0  Chicago      1  Havana      2  Las Vegas      3  Rio      4  exit
choice /c 01234 /n /t 10 /d 4 /m "  select 0-4 (exits after 10s): "
set "PICKC=%ERRORLEVEL%"
type nul > "%STOPF%"
set "CITY="
if "%PICKC%"=="1" set "CITY=chicago"
if "%PICKC%"=="2" set "CITY=havana"
if "%PICKC%"=="3" set "CITY=lasvegas"
if "%PICKC%"=="4" set "CITY=rio"
if "%CITY%"=="" (echo   exit - not launching. & exit /b 0)
if not exist "%EXEDIR%\JERICHO_dev.exe" (echo FAILED: no game at %EXEDIR% & exit /b 1)
rem point carhacks.ini's import at the picked car: resident slot 5 = this bank
python "%CAR%\register_city.py" @NAME@ "%MIRROR%\@NAME@.LEV" "%EXEDIR%" --use !PSLOT!
echo   launching Take a Ride in %CITY% with @NAME@ !PCAR!...
pushd "%EXEDIR%"
start "" "JERICHO_dev.exe" -nointro -level %CITY% -car !PSLOT! -gamemode takeadrive -weather none -time day
popd
exit /b 0
:failed
echo  ==== BAKE FAILED - nothing was installed ====
powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 8"
exit /b 1
