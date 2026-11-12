@echo off
rem Shared bootstrap (called by run_daily.bat / run_webapp.bat):
rem locate the repo root and resolve the Python interpreter, so no
rem machine-specific path is hardcoded in startup scripts. Works on any
rem machine right after clone.
rem Resolution order (first hit wins):
rem   1. env var QUANTDEX_PYTHON (full path to python.exe; Task Scheduler can read it too)
rem   2. scripts\python_path.local.bat (per-machine override, gitignored; one line: set "PY=...")
rem   3. repo-root .venv\Scripts\python.exe (plain venv)
rem   4. QuantDex conda env under common install locations (miniconda3/anaconda3/miniforge3)
rem   5. conda found on PATH -> derive its envs\QuantDex from its own location
rem   6. fallback: python on PATH (README recommends "conda activate QuantDex" first)
rem NOTE: do NOT use setlocal here - ROOT/PY must propagate back to the caller.
rem NOTE: keep this file ASCII-only; UTF-8 Chinese text misparses on cp936 consoles.

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"
if not exist data\store\logs mkdir data\store\logs

if defined QUANTDEX_PYTHON (
    set "PY=%QUANTDEX_PYTHON%"
    goto :resolve_done
)

if exist "%~dp0python_path.local.bat" call "%~dp0python_path.local.bat"
if defined PY goto :resolve_done

if exist "%ROOT%\.venv\Scripts\python.exe" (
    set "PY=%ROOT%\.venv\Scripts\python.exe"
    goto :resolve_done
)

for %%D in (
    "%USERPROFILE%\miniconda3\envs\QuantDex"
    "%USERPROFILE%\anaconda3\envs\QuantDex"
    "%USERPROFILE%\miniforge3\envs\QuantDex"
    "C:\ProgramData\miniconda3\envs\QuantDex"
    "C:\ProgramData\anaconda3\envs\QuantDex"
) do if exist "%%~D\python.exe" (
    set "PY=%%~D\python.exe"
    goto :resolve_done
)

rem conda on PATH -> derive envs dir from the conda binary location
rem (handles custom install drives, e.g. D:\conda\miniconda3)
for /f "delims=" %%C in ('where conda 2^>nul') do (
    if exist "%%~dpC..\envs\QuantDex\python.exe" (
        set "PY=%%~dpC..\envs\QuantDex\python.exe"
        goto :resolve_done
    )
)

set "PY=python"

:resolve_done
rem validate: path-like value (contains backslash) -> if exist;
rem bare name like "python" -> where (note: where rejects full paths)
if "%PY%"=="%PY:\=%" (
    where "%PY%" >nul 2>nul
    if errorlevel 1 goto :py_missing
) else if not exist "%PY%" goto :py_missing
exit /b 0

:py_missing
echo [QuantDex] Python interpreter not found: "%PY%"
echo [QuantDex] Fix with any ONE of the following - see README section 3 and scripts\README.md:
echo   1. set env var QUANTDEX_PYTHON to your python.exe full path
echo   2. create scripts\python_path.local.bat with one line: set "PY=C:\path\to\python.exe"
echo   3. create the conda env "QuantDex" per README, then retry
exit /b 1
