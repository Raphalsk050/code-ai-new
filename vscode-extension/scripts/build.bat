@echo off
rem Builds the VSCode extension into installable VSIX packages.
rem
rem Each package carries the frozen code-ai backend, so installing it needs
rem nothing else on the machine - no Python, no pip, no PATH entry. Nothing has
rem to be prepared first: a binary that is not there is built, with PyInstaller
rem for this platform and in a container for the other one.
setlocal

if /i "%~1"=="-h" goto usage
if /i "%~1"=="--help" goto usage
if /i "%~1"=="/?" goto usage

rem The extension root is this script's parent, so the build works from any cwd.
pushd "%~dp0.."

where node >nul 2>&1
if errorlevel 1 (
    echo node not found on PATH. Install Node 18 or newer. 1>&2
    goto fail
)
where npm >nul 2>&1
if errorlevel 1 (
    echo npm not found on PATH. 1>&2
    goto fail
)

rem vsce, esbuild and typescript all live in devDependencies, so a fresh
rem checkout needs one install before anything can be built.
if not exist "node_modules" (
    echo Installing build dependencies...
    call npm install --no-audit --no-fund
    if errorlevel 1 goto fail
)

call npm run typecheck
if errorlevel 1 goto fail

node scripts\package.mjs %*
if errorlevel 1 goto fail

popd
exit /b 0

:fail
echo.
echo Build failed - the reason is in the output above. 1>&2
popd
exit /b 1

:usage
echo Usage: scripts\build.bat [options]
echo.
echo Builds everything it can and says what it could not. No arguments needed.
echo.
echo   --target LIST       targets to build (default: both; e.g. linux-x64 or
echo                       win32-x64,linux-x64^). A target named here must succeed.
echo   --win PATH          use this prebuilt Windows binary instead of building one
echo   --linux PATH        use this prebuilt Linux binary instead of building one
echo   --refresh-backend   rebuild the backend even if it looks up to date
echo   --verify-ssl        keep pip's certificate checks on
echo   --skip-verify       do not check the package afterwards (not recommended^)
echo.
echo The backend is built from packaging\code-ai.spec into an isolated
echo .build-venv, and reused while it is newer than src\. Building the *other*
echo platform's binary needs Docker; without it that target is skipped, and the
echo build-binaries workflow produces one to pass with --win/--linux.
exit /b 2
