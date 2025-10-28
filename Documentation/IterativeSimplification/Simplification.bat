::This script was created by Epic Games Slovakia

:: Switch off console output.
@echo off

call SetVariables.bat

:: A path to folder containing file with simplification parameters
set SimplificationParameters=%RootFolder%simplificationParameters.xml

set Iterations=3

FOR /L %%A IN (1,1,%Iterations%) DO (
    :: Run RealityCapture
    %RealityCaptureExe% -delegateTo * ^
            -simplify %SimplificationParameters%
)
