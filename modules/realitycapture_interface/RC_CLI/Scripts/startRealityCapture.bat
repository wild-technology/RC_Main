:: These scripts were created by Epic Games Slovakia, who doesn't carry any liability in case issues related to the sample occur.
@echo off
@echo off
REM RealityScan 2.0 doesn't need separate startup
REM Commands are executed directly in batch without delegation
echo RealityScan will be invoked with command batch
exit /b 0
rem Allow override via environment variable RC_EXECUTABLE
if defined RC_EXECUTABLE (
    set RealityCapture="%RC_EXECUTABLE%"
) else (
    set RealityCapture="C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe"
)

:: Test the RealityCapture is running
%RealityCapture% -getStatus *
IF /I "%ERRORLEVEL%"=="0" (
    
    echo RealityCapture instance is already running
    %RealityCapture% -delegateTo RC1 -newScene -deleteAutosave
    goto :eof
)

echo Starting new RealityCapture instance

start "" %RealityCapture% -headless -stdConsole -silent "%ErrorPath%" -setInstanceName RC1 -set "appAutoSaveMode=false" -set "RealityCaptureAutoSaveCliHandling=delete" -set "RealityCaptureQuitOnError=false" -set "RealityCaptureProcessActionTime=0" -set "RealityCaptureProcessAction=ExecuteProgram" -writeProgress "%ErrorPath%\progress.txt" 600 -set "RealityCaptureProcessExecCmd=%ErrorWriter% $(processResult) $(processId) $(processDuration:d) %ErrorPath%\\errors.txt"

echo Waiting until the RealityCapture instance starts

:waitStart
%RealityCapture% -getStatus *
IF /I "%ERRORLEVEL%" NEQ "0" (
    goto :waitStart
)

:eof