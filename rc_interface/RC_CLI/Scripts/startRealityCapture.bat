:: These scripts were created by Epic Games Slovakia, who doesn't carry any liability in case issues related to the sample occur.
@echo off

set RealityCapture = "C:\Program Files\Capturing Reality\RealityCapture\RealityCapture.exe" 

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