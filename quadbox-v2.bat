@echo off
setlocal EnableExtensions
title Quadbox v2
cd /d "%~dp0"
if not exist state mkdir state

echo ============================================================
echo   Quadbox v2  (3 questers + 1 booster)
echo ============================================================
echo.

REM ---- 0. Self-update from GitHub (same as wiz101.bat) ----------------------
REM config.yaml, state, quad and .venv are never touched (git-ignored).
set "REPO=https://github.com/szatcg/wiz101-auto.git"
if /i "%~1"=="--updated" goto :noupdate
where git >nul 2>&1 || goto :noupdate
echo [0/3] Checking for updates...
if not exist .git (
  git init -q
  git remote add origin "%REPO%"
  git fetch -q origin main && (
    git reset -q --hard origin/main
    git branch -q -M main
    git branch -q -u origin/main main
    "%~f0" --updated
    exit /b
  ) || (
    echo       could not reach GitHub; using the files already here.
  )
  goto :noupdate
)
git diff --quiet && git diff --cached --quiet || (
  echo       local changes present; skipping the automatic update.
  goto :noupdate
)
git pull -q --ff-only origin main && (
  for /f %%h in ('git rev-parse --short HEAD') do echo       on version %%h
  "%~f0" --updated
  exit /b
) || (
  echo       could not update from GitHub; using the files already here.
)
:noupdate

REM ---- 1. Python and Git ----
echo [1/3] Checking Python and Git...
set "PY="
for %%v in (3.14 3.13 3.15 3.16) do (
  if not defined PY (
    py -%%v -c "import sys" >nul 2>&1 && set "PY=py -%%v"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  where py >nul 2>&1 && (
    echo       Installing Python 3.13 with the Python Install Manager...
    py install 3.13
    py -3.13 -c "import sys" >nul 2>&1 && set "PY=py -3.13"
  )
)
if not defined PY (
  echo.
  echo ERROR: Python 3.13 or newer is not installed.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  goto :fail
)
where git >nul 2>&1
if errorlevel 1 (
  echo.
  echo ERROR: Git is not installed. Get it from https://git-scm.com/download/win
  echo then restart your PC and run this again.
  goto :fail
)
echo       ok.

REM ---- 2. Install / update the bot ----
echo [2/3] Checking the bot install...
set "NEED_INSTALL="
if not exist ".venv\Scripts\python.exe" set "NEED_INSTALL=1"
if not defined NEED_INSTALL (
  ".venv\Scripts\python.exe" -c "import wizwalker, wiz101_auto" >nul 2>&1 || set "NEED_INSTALL=1"
)
if not defined NEED_INSTALL (
  fc /b pyproject.toml ".venv\installed-pyproject.toml" >nul 2>&1 || set "NEED_INSTALL=1"
)
if defined NEED_INSTALL goto :install
echo       up to date.
goto :configured

:install
echo       Installing. The first time takes a few minutes (log: state\setup.txt)
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv > state\setup.txt 2>&1 || goto :installfail
)
".venv\Scripts\python.exe" -m pip install --upgrade pip >> state\setup.txt 2>&1
".venv\Scripts\python.exe" -m pip install -e . >> state\setup.txt 2>&1 || goto :installfail
".venv\Scripts\python.exe" -c "import wizwalker, wiz101_auto" >> state\setup.txt 2>&1 || goto :installfail
copy /y pyproject.toml ".venv\installed-pyproject.toml" >nul
echo       installed.

:configured
if not exist config.yaml (
  copy configs\quadbox.yaml config.yaml >nul
  echo       created config.yaml: put your questers' names at the bottom ^(see START HERE.txt^)
)
findstr /b /c:"quadbox:" config.yaml >nul 2>&1 || (
  >> config.yaml echo.
  >> config.yaml echo quadbox:
  >> config.yaml echo   follow: q1
  >> config.yaml echo   # Your questers' names exactly as the Friends list shows them:
  >> config.yaml echo   names: {q1: "Name One", q2: "Name Two", q3: "Name Three"}
  echo       added a quadbox section to the bottom of config.yaml: put your questers' names there
)
set "BOT=.venv\Scripts\python.exe -m wiz101_auto"

REM ---- 3. Menu ----
echo [3/3] Ready. Open all 4 games, log in and stand the wizards in the world.
echo       ^(The first time, open the questers in q1, q2, q3 order.^)
echo.
echo   [1] Start quadbox                    (starts automatically in 8 seconds)
echo   [2] Save the logins   - a frozen or crashed game then restarts by itself
echo   [3] Stop all 4
echo   [4] Show the quest window (opens by itself when quadbox starts)
echo   [5] Open config.yaml in Notepad
echo.
echo   While it runs: Ctrl+Shift+Q = stop all 4, Ctrl+Shift+P = pause/resume
echo   Don't close the window with the X.
echo.
choice /c 12345 /t 8 /d 1 /n /m "Choose 1-5: "
set "CHOICE=%errorlevel%"
echo.
if "%CHOICE%"=="1" goto :run
if "%CHOICE%"=="2" goto :logins
if "%CHOICE%"=="3" goto :stop
if "%CHOICE%"=="4" goto :panel
if "%CHOICE%"=="5" goto :edit
goto :done

:run
%BOT% quadbox run -c config.yaml
goto :done

:logins
%BOT% quadbox logins
goto :done

:stop
%BOT% quadbox stop
goto :done

:panel
start "" .venv\Scripts\pythonw.exe -m wiz101_auto quadbox panel -c config.yaml
exit /b 0

:edit
start "" notepad config.yaml
exit /b 0

:installfail
echo.
echo ERROR: installing the bot failed. Last lines of state\setup.txt:
powershell -NoProfile -Command "Get-Content 'state\setup.txt' -Tail 25"
goto :fail

:fail
echo.
echo Send state\setup.txt (if it exists) and a screenshot of this window to Claude.
pause
exit /b 1

:done
echo.
pause
exit /b 0
