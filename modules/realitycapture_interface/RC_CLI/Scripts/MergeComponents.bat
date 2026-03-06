@echo off

REM MergeComponents.bat - Merge components in saved project
REM Python handles component export separately

echo ========================================
echo Merging Components
echo ========================================

if "%~1"=="" (
    echo [ERROR] Project file path required
    exit /b 1
)

set "project_path=%~1"
set "min_size=%~2"

if "%min_size%"=="" (
    set "min_size=100"
)

if not exist "%project_path%" (
    echo [ERROR] Project file not found: %project_path%
    exit /b 1
)

echo Project: %project_path%
echo Minimum component size: %min_size% images
echo.

REM Terminate existing instances
tasklist /FI "IMAGENAME eq RealityScan.exe" 2>NUL | find /I /N "RealityScan.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo Killing existing RealityScan instance
    taskkill /F /IM RealityScan.exe >NUL 2>&1
    timeout /t 3 /nobreak >NUL
)

REM Call SetVariables to get RC path
set RootFolder=%~dp0..\
call SetVariables.bat

echo [INFO] Merging components with min size %min_size%...
"%RealityCapture%" ^
    -load "%project_path%" ^
    -setMinComponentSize %min_size% ^
    -mergeComponents ^
    -save "%project_path%" ^
    -quit

if ERRORLEVEL 1 (
    echo [ERROR] Component merge failed with exit code %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)

echo ========================================
echo MERGE COMPLETE
echo ========================================
echo Merged project saved: %project_path%
echo.

exit /b 0