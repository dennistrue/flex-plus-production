@echo off
setlocal

set "REPO_DIR=%~dp0"

:restart
set "HEAD_BEFORE="
set "HEAD_AFTER="
if exist "%REPO_DIR%\.git" (
    where git >nul 2>nul
    if not errorlevel 1 (
        pushd "%REPO_DIR%"
        for /f %%H in ('git rev-parse HEAD 2^>nul') do set "HEAD_BEFORE=%%H"
        git fetch --quiet --tags >nul 2>&1
        git pull --ff-only
        for /f %%H in ('git rev-parse HEAD 2^>nul') do set "HEAD_AFTER=%%H"
        popd
        if defined HEAD_BEFORE if defined HEAD_AFTER if /I not "%HEAD_BEFORE%"=="%HEAD_AFTER%" (
            goto restart
        )
    )
)

cd /d "%REPO_DIR%bin"

set PYTHON_BIN=%FLEX_PYTHON%
if "%PYTHON_BIN%"=="" set PYTHON_BIN=python3

where "%PYTHON_BIN%" >nul 2>nul
if errorlevel 1 (
    set PYTHON_BIN=py
    where "%PYTHON_BIN%" >nul 2>nul
    if errorlevel 1 (
        echo python3 or py was not found. Install Python 3 and try again.
        pause
        exit /b 1
    )
)

"%PYTHON_BIN%" "%~dp0bin\flash_gui.py"
if errorlevel 1 (
    echo Flex Plus Flash GUI exited with an error.
    pause
)
