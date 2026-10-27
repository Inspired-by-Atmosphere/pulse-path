@echo off
REM pulse-path - 60-second demo, runnable from ANY working directory.
REM
REM   examples\quickstart.bat
REM
REM 1. locates the repository root from this script's own location
REM 2. builds a throwaway sandbox in %TEMP% and copies the example pointer file
REM 3. runs mem_guard.py --check --verbose  (dry run: reports, never rewrites)
REM 4. runs scan_links.py over skills\
REM 5. runs scripts\run_checks.py          (the unified self-check entry point)
REM
REM The two dangling pointers reported by mem_guard in step 3 are intentional:
REM examples\MEMORY.sample.md ships them so the demo shows the guard catching
REM broken links. The sandbox is deleted on exit.
REM
REM Override the interpreter with:  set PYTHON=C:\path\to\python.exe

setlocal
cd /d "%~dp0.."
if errorlevel 1 (
    echo ERROR: cannot cd to the repository root
    exit /b 1
)

set "PY=python"
if defined PYTHON set "PY=%PYTHON%"

echo == pulse-path quickstart ==
echo repo root : %CD%
%PY% -c "import sys;print('python    : python ' + sys.version.split()[0])"
if errorlevel 1 (
    echo ERROR: no working python on PATH ^(install Python 3.10+ or set PYTHON=...^)
    exit /b 1
)

set "SANDBOX=%TEMP%\pp-quickstart-%RANDOM%%RANDOM%"
echo sandbox   : %SANDBOX%
echo.

mkdir "%SANDBOX%\memories"
if errorlevel 1 (
    echo ERROR: cannot create %SANDBOX%\memories
    goto :cleanup_fail
)
copy /y "examples\MEMORY.sample.md" "%SANDBOX%\memories\MEMORY.md" >nul
if errorlevel 1 (
    echo ERROR: cannot copy examples\MEMORY.sample.md
    goto :cleanup_fail
)
> "%SANDBOX%\memories\USER.md" echo # User profile
>>"%SANDBOX%\memories\USER.md" echo - Demo line
if not exist "%SANDBOX%\memories\USER.md" (
    echo ERROR: cannot write %SANDBOX%\memories\USER.md
    goto :cleanup_fail
)

set "FAILED=0"

echo -- 1/3 mem_guard --check --verbose (dry run) --
set "HERMES_HOME=%SANDBOX%"
%PY% "skills\memory-pointer-system\scripts\mem_guard.py" --check --verbose
if errorlevel 1 (
    echo   - mem_guard FAILED
    set "FAILED=1"
)
echo.

echo -- 2/3 scan_links.py skills --
set "SKILLS_ROOT=skills"
%PY% "skills\pulse-path\scripts\scan_links.py" skills
if errorlevel 1 (
    echo   - scan_links FAILED
    set "FAILED=1"
)
echo.

echo -- 3/3 scripts\run_checks.py (unified self-check) --
%PY% "scripts\run_checks.py"
if errorlevel 1 (
    echo   - run_checks FAILED
    set "FAILED=1"
)
echo.

if "%FAILED%"=="1" (
    echo QUICKSTART FAILED - see the failing step above
    goto :cleanup_fail
)

echo QUICKSTART OK
set "RC=0"
goto :cleanup

:cleanup_fail
set "RC=1"

:cleanup
if defined SANDBOX rmdir /s /q "%SANDBOX%" >nul 2>&1
endlocal & exit /b %RC%
