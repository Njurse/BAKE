@echo off
rem ============================================================================
rem  playtest.bat  -  Take a Ride with a car from the @NAME@ bank (no bake)
rem
rem  GENERATED from bake\tools\car\template_playtest.bat by bank_write_scripts.py.
rem  Asks which car and which city, points carhacks.ini's
rem  "import = 5:<bank city>:<car slot>" at it and launches. @NAME@ is a car
rem  bank, not a level: the ride runs in a real city. Run rebake.bat first
rem  after any change to the bank.
rem ============================================================================
setlocal EnableDelayedExpansion
set "BANK=%~dp0"
set "BANK=%BANK:~0,-1%"
set "CAR=%BANK%\@TOOLS@"
set "EXEDIR=@EXEDIR@"
set "MIRROR=@MIRROR@"
if not exist "%EXEDIR%\JERICHO_dev.exe" (echo FAILED: no game at %EXEDIR% & exit /b 1)
if not exist "%MIRROR%\@NAME@.LEV" (echo FAILED: no %MIRROR%\@NAME@.LEV - run rebake.bat first & exit /b 1)
python "%CAR%\register_city.py" @NAME@ "%MIRROR%\@NAME@.LEV" "%EXEDIR%"
set /a N=0
for /f "usebackq tokens=1,2" %%A in (`python -c "import json;[print(o['slot'],o['car']) for o in json.load(open(r'%BANK%\bank.json'))['declare']['order']]"`) do (
    set /a N+=1
    set "SLOT!N!=%%A"
    set "ID!N!=%%B"
    echo     !N!  %%B  ^(slot %%A^)
)
set "PICK=1"
if !N! GTR 1 set /p "PICK=  car [1]: "
if "!PICK!"=="" set "PICK=1"
set "PCAR=!ID%PICK%!"
set "PSLOT=!SLOT%PICK%!"
if "!PCAR!"=="" (set "PCAR=!ID1!" & set "PSLOT=!SLOT1!")
echo.
echo   Take a Ride with !PCAR! - in which city?
echo     0  Chicago      1  Havana      2  Las Vegas      3  Rio      4  exit
choice /c 01234 /n /m "  select 0-4: "
set "PICKC=%ERRORLEVEL%"
set "CITY="
if "%PICKC%"=="1" set "CITY=chicago"
if "%PICKC%"=="2" set "CITY=havana"
if "%PICKC%"=="3" set "CITY=lasvegas"
if "%PICKC%"=="4" set "CITY=rio"
if "%CITY%"=="" (echo   exit - not launching. & exit /b 0)
python "%CAR%\register_city.py" @NAME@ "%MIRROR%\@NAME@.LEV" "%EXEDIR%" --use !PSLOT!
echo   launching Take a Ride in %CITY% with @NAME@ !PCAR!...
pushd "%EXEDIR%"
start "" "JERICHO_dev.exe" -nointro -level %CITY% -car !PSLOT! -gamemode takeadrive -weather none -time day
popd
exit /b 0
