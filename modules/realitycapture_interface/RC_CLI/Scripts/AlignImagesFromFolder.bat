@echo off

call SetVariables.bat
echo Setting Variables

if [%1] == [] (
    set /P input_dir="Image Input Directory: "
) else (
    set input_dir=%~1
)

if [%2] == [] (
    set /P input_dir="Component Output Directory: "
) else (
    set output_dir=%~2
)

if [%3] == [] (
    set /P flight_log_dir="Path to Flight Log (or empty if no flight log): "
) else (
    set flight_log_dir=%~3
)

if not "flight_log_dir" == "" if [%4] == [] (
    set /P flight_log_params_dir="Path to Flight Log Params: "
) else (
    set flight_log_params_dir=%~4
)

echo Starting Reality Capture

call startRealityCapture.bat

%RealityCapture% -delegateTo RC1 -addFolder "%input_dir%"

if not "flight_log_dir" == "" (
    %RealityCapture% -delegateTo RC1 -importFlightLog "%flight_log_dir%" "%flight_log_params_dir%"
)

%RealityCapture% -delegateTo RC1 -align
%RealityCapture% -delegateTo RC1 -exportXMP
%RealityCapture% -delegateTo RC1 -selectMaximalComponent
%RealityCapture% -delegateTo RC1 -exportSelectedComponentDir "%output_dir%"
%RealityCapture% -delegateTo RC1 -quit