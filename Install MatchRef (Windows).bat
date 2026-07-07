@echo off
REM ===========================================================================
REM  MatchRef - one-click installer for DaVinci Resolve (Windows).
REM
REM  Double-click this file. It creates a local Python environment, installs the
REM  dependencies, and copies MatchRef into the DaVinci Resolve scripts folder.
REM
REM  After it finishes, open Resolve: Workspace > Scripts > Utility > MatchRef
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================
echo   MatchRef installer for DaVinci Resolve
echo ============================================
echo.

REM --- 1. Python 3 must be available ---------------------------------------
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3 is required but was not found.
  echo Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^),
  echo then double-click this installer again.
  goto :fail
)

REM MatchRef needs Python 3.10+ ^(zip^(strict=^), PEP 604 unions^). A too-old
REM Python produces a venv that fails at runtime, so stop here with a clear note.
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo Python 3.10 or newer is required, but an older version was found.
  for /f "delims=" %%v in ('python -c "import sys; print(sys.version.split()[0])"') do echo Found Python %%v
  echo Install Python 3.10+ from https://www.python.org/downloads/ and re-run.
  goto :fail
)

REM --- 2. Virtual environment + dependencies -------------------------------
echo - Setting up the Python environment ^(this can take a minute^)...
if not exist ".venv" (
  python -m venv .venv || goto :fail
)
call ".venv\Scripts\activate.bat" || goto :fail
python -m pip install --upgrade pip || goto :fail
python -m pip install -r requirements.txt || goto :fail

REM --- 3. Copy into the Resolve Scripts\Utility folder ---------------------
set "DEST=%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility"
echo.
echo - Installing into DaVinci Resolve...
if not exist "%DEST%" mkdir "%DEST%"

REM robocopy returns 0-7 on success; treat >=8 as failure.
robocopy "%~dp0." "%DEST%\matchref" /E /NFL /NDL /NJH /NJS /NP ^
  /XD ".git" ".venv" "__pycache__" "debug" ".pytest_cache" ".ruff_cache" ^
  /XF ".DS_Store" >nul
if errorlevel 8 goto :fail

robocopy "%~dp0.venv" "%DEST%\matchref\.venv" /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :fail

copy /Y "%~dp0scripts\MatchRef.py" "%DEST%\MatchRef.py" >nul || goto :fail

echo.
echo [OK] MatchRef is installed.
echo.
echo In DaVinci Resolve open:  Workspace ^> Scripts ^> Utility ^> MatchRef
echo.
pause
exit /b 0

:fail
echo.
echo [FAILED] Installation did not complete - see the error above.
echo.
pause
exit /b 1
