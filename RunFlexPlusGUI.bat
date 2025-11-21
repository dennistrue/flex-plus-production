@echo off
setlocal

set "REPO_DIR=%~dp0"
rem Use a version without a trailing backslash to avoid git -C parsing issues
set "REPO_DIR_GIT=%REPO_DIR%."
set "BIN_DIR=%REPO_DIR%bin"
set "LOG_DIR=%BIN_DIR%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul

set "RUN_ID=%date:/=%_%time::=%"
set "RUN_ID=%RUN_ID: =%"
set "RUN_ID=%RUN_ID:.=%"
set "LOG_FILE=%LOG_DIR%\flash_gui_launcher_%RUN_ID%.log"

echo ==== %date% %time% ==== >> "%LOG_FILE%"

set "GUI_SCRIPT=%BIN_DIR%\flash_gui.py"

setlocal enabledelayedexpansion
goto :main

:log
echo %~1
echo %~1 >> "%LOG_FILE%"
goto :eof

:main
call :log "PATH: %PATH%"

where git >nul 2>nul
if errorlevel 1 (
    call :log "Git is required but was not found. Please install Git for Windows and rerun."
    exit /b 1
)

set "HEAD_BEFORE="
for /f "usebackq delims=" %%H in (`git -C "%REPO_DIR_GIT%" rev-parse HEAD 2^>nul`) do set "HEAD_BEFORE=%%H"

set "GIT_BRANCH="
for /f "usebackq delims=" %%B in (`git -C "%REPO_DIR_GIT%" rev-parse --abbrev-ref HEAD 2^>nul`) do set "GIT_BRANCH=%%B"

if /i "!GIT_BRANCH!"=="HEAD" (
    call :log "Repository is in detached HEAD; skipping auto-update."
) else (
    call :log "Checking for updates..."
    git -C "%REPO_DIR_GIT%" fetch --tags --quiet >> "%LOG_FILE%" 2>&1
    if errorlevel 1 (
        call :log "git fetch failed. Check network/credentials and retry. See log at %LOG_FILE%"
        exit /b 1
    )
    git -C "%REPO_DIR_GIT%" pull --ff-only >> "%LOG_FILE%" 2>&1
    if errorlevel 1 (
        call :log "git pull failed. Resolve Git issues and retry. See log at %LOG_FILE%"
        exit /b 1
    )

    set "HEAD_AFTER="
    for /f "usebackq delims=" %%H in (`git -C "%REPO_DIR_GIT%" rev-parse HEAD 2^>nul`) do set "HEAD_AFTER=%%H"

    if not "%HEAD_BEFORE%"=="" if not "%HEAD_AFTER%"=="" if not "%HEAD_AFTER%"=="%HEAD_BEFORE%" (
        call :log "Repository updated; restarting launcher to pick up changes..."
        "%~f0" %*
        exit /b
    )
)

cd /d "%BIN_DIR%"

echo Detecting Python...
echo PATH is: %PATH%
set "PYTHON_BIN=%MAIN_HUB_PYTHON%"
if "%PYTHON_BIN%"=="" set "PYTHON_BIN=python"

call :log "Detecting Python (initial): %PYTHON_BIN%"
where "%PYTHON_BIN%" >nul 2>nul
if errorlevel 1 (
    call :log "%PYTHON_BIN% not found. Trying \"py\"..."
    set "PYTHON_BIN=py"
    where "%PYTHON_BIN%" >nul 2>nul
)

if errorlevel 1 (
    call :log "%PYTHON_BIN% not found. Trying \"python3\"..."
    set "PYTHON_BIN=python3"
    where "%PYTHON_BIN%" >nul 2>nul
)

call :log "Validating Python with --version"
"%PYTHON_BIN%" --version >nul 2>nul
if errorlevel 1 (
    call :log "%PYTHON_BIN% failed; attempting winget install of Python 3.13..."
    winget install --id Python.Python.3.13 -e --source winget --accept-source-agreements --accept-package-agreements >> "%LOG_FILE%" 2>&1
    call :log "winget exited with code %errorlevel%"
    call :log "Re-checking for Python after install..."
    for %%P in (python py python3) do (
        where "%%P" >nul 2>nul
        if not errorlevel 1 (
            "%%P" --version >nul 2>nul
            if not errorlevel 1 (
                set "PYTHON_BIN=%%P"
                goto found_python
            )
        )
    )
    call :log "Python is still not available. Install Python 3 (try \"winget install Python.Python.3.13\") and rerun. Log: %LOG_FILE%"
    exit /b 1
)

:found_python
where "%PYTHON_BIN%" >nul 2>nul
if errorlevel 1 (
    call :log "Python is still not available. Install Python 3 (try \"winget install Python.Python.3.13\") and rerun. Log: %LOG_FILE%"
    exit /b 1
)
"%PYTHON_BIN%" --version >nul 2>nul
if errorlevel 1 (
    call :log "Python is present on PATH but failed to run. Install Python 3 (try \"winget install Python.Python.3.13\") and rerun. Log: %LOG_FILE%"
    exit /b 1
)

call :log "Using Python interpreter: %PYTHON_BIN%"
call :log "Starting GUI in 3 seconds... (Ctrl+C to cancel)"
ping -n 4 127.0.0.1 >nul

"%PYTHON_BIN%" "%GUI_SCRIPT%" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    call :log "Flash GUI exited with an error."
) else (
    call :log "Flash GUI completed."
)
exit /b %errorlevel%
