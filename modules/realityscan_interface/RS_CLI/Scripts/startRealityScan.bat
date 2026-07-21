:: Boots (or attaches to) the headless RealityScan instance %RS_INSTANCE%.
:: Based on the Epic Games Slovakia CLI samples, adapted for RealityScan 2.2.
::
:: The instance is started with RealityScan's built-in monitoring hooks:
::   -writeProgress          progress stream tailed by the Python orchestrator
::   appProcessAction /      RealityScan itself runs ErrorWriter.bat when a
::   appProcessExecCmd       process finishes, logging every completion to
::                           results.log and failures to errors.txt
@echo off

if not defined RealityScan call "%~dp0SetVariables.bat"
if not defined RealityScan exit /b 1

:: Test whether our instance is already running
%RealityScan% -getStatus %RS_INSTANCE% >nul 2>&1
IF /I "%ERRORLEVEL%"=="0" (
    echo RealityScan instance %RS_INSTANCE% is already running - reusing it with a fresh scene
    %RealityScan% -delegateTo %RS_INSTANCE% -newScene -deleteAutosave
    goto :eof
)

echo Starting new RealityScan instance %RS_INSTANCE%

:: Optional GPU pinning: RS_GPU_DEVICES (e.g. "0" or "0,1") restricts the
:: CUDA devices visible to this instance. Unset = use all GPUs.
if defined RS_GPU_DEVICES set CUDA_VISIBLE_DEVICES=%RS_GPU_DEVICES%

start "" %RealityScan% -headless -stdConsole -silent "%ErrorPath%" -setInstanceName %RS_INSTANCE% -set "appAutoSaveMode=false" -set "appQuitOnError=false" -set "appProcessActionTime=0" -set "appProcessAction=ExecuteProgram" -set "appProcessExecCmd=%ErrorWriter% $(processResult) $(processId) $(processDuration:d) %ErrorPath%" -writeProgress "%ErrorPath%\progress.txt" 600

echo Waiting until the RealityScan instance %RS_INSTANCE% is ready

set /a startTries=0
:waitStart
%RealityScan% -getStatus %RS_INSTANCE% >nul 2>&1
IF /I "%ERRORLEVEL%" NEQ "0" (
    set /a startTries+=1
    if %startTries% GEQ 120 (
        echo ERROR: RealityScan instance %RS_INSTANCE% did not become ready within 120 seconds
        exit /b 1
    )
    ping -n 2 127.0.0.1 >nul
    goto :waitStart
)

:eof
