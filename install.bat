@echo off
rem Installs Code-AI from source into a virtualenv.
rem
rem The company network re-signs TLS with its own certificate, so pip is told
rem to trust the PyPI hosts by default - the same two hosts the browser
rem installer trusts (src\code_ai\tools\browser\install.py). Pass --verify-ssl
rem on a network that leaves certificates alone.
setlocal

rem Captured before the first shift, which moves %0 along with the arguments.
set "ROOT=%~dp0"

set "VENV=.venv"
set "EXTRAS=dev"
set "BROWSER=0"
set "TRUSTED=--trusted-host pypi.org --trusted-host files.pythonhosted.org"

:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--extras" (set "EXTRAS=%~2" & shift & shift & goto parse)
if /i "%~1"=="--venv" (set "VENV=%~2" & shift & shift & goto parse)
if /i "%~1"=="--browser" (set "BROWSER=1" & shift & goto parse)
if /i "%~1"=="--verify-ssl" (set "TRUSTED=" & shift & goto parse)
if /i "%~1"=="-h" goto usage
if /i "%~1"=="--help" goto usage
echo unknown option: %~1 1>&2
goto usage
:parsed

rem Newest first, and the version is checked rather than assumed: a "python"
rem that is 3.9 would fail much later, inside the build.
set "PYTHON="
for %%c in ("py -3.13" "py -3.12" "py -3.11" "py -3" "python") do (
    if not defined PYTHON (
        %%~c -c "import sys; raise SystemExit(sys.version_info < (3, 11))" >nul 2>&1 && set "PYTHON=%%~c"
    )
)
if not defined PYTHON (
    echo Python 3.11+ not found on PATH. 1>&2
    exit /b 1
)

pushd "%ROOT%"

if not exist "%VENV%\Scripts\python.exe" (
    %PYTHON% -m venv "%VENV%"
    if errorlevel 1 goto fail
)
set "VPY=%VENV%\Scripts\python.exe"

"%VPY%" -m pip install %TRUSTED% --upgrade pip
if errorlevel 1 goto fail
"%VPY%" -m pip install %TRUSTED% -e ".[%EXTRAS%]"
if errorlevel 1 goto fail

rem Chromium is a few hundred MB and is normally fetched on first use; doing it
rem here means the first browsing turn is not the one that waits for it.
if "%BROWSER%"=="1" (
    "%VPY%" -m code_ai doctor browser --install
    if errorlevel 1 goto fail
)

echo.
echo Installed. Activate with: %VENV%\Scripts\activate.bat
echo Then run: code-ai
popd
exit /b 0

:fail
echo.
echo Install failed - the reason is in the output above. 1>&2
popd
exit /b 1

:usage
echo Usage: install.bat [--extras LIST] [--venv DIR] [--browser] [--verify-ssl]
exit /b 2
