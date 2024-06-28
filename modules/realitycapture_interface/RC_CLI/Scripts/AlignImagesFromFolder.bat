@echo off

echo Reading default variables
call SetVariables.bat

echo Setting rc_main variables

if [%1] == [] (
    set /P input_dir="Image Input Directory: "
) else (
    set input_dir=%~1
)

if [%2] == [] (
    set /P output_dir="Component Output Directory: "
) else (
    set output_dir=%~2
)

if [%3] == [] (
    set /P flight_log_dir="Path to Flight Log (or empty if no flight log): "
) else (
    set flight_log_dir=%~3
)

if not [flight_log_dir] == [] if [%4] == [] (
    set /P flight_log_params_dir="Path to Flight Log Params: "
) else (
    set flight_log_params_dir=%~4
)

if [%5] == [] (
    CHOICE /C YN /M "Generate Model (Y/N):"
    set generate_model=%ERRORLEVEL%
) else (
    set generate_model=%~5
)

set "GENERATE_MODEL_BOOL="
if [%generate_model%] == [Y] set GENERATE_MODEL_BOOL=1
if [%generate_model%] == [true] set GENERATE_MODEL_BOOL=1

if defined GENERATE_MODEL_BOOL (
    if [%6] == [] (
        CHOICE /C YN /M "Cull Polygons (Y/N):"
        set cull_polygons=%ERRORLEVEL%
    ) else (
        set cull_polygons=%~6
    )

    if [%7] == [] (
        set /P scene_name="Scene name:"
    ) else (
        set scene_name=%~7
    )
)

set "CULL_POLYGONS_BOOL="
if [%cull_polygons%] == [Y] set CULL_POLYGONS_BOOL=1
if [%cull_polygons%] == [true] set CULL_POLYGONS_BOOL=1

echo Starting Reality Capture

call startRealityCapture.bat

%RealityCapture% -delegateTo RC1 -addFolder "%input_dir%"

if not [flight_log_dir] == [] (
    %RealityCapture% -delegateTo RC1 -importFlightLog "%flight_log_dir%" "%flight_log_params_dir%"
)

%RealityCapture% -delegateTo RC1 -align
%RealityCapture% -delegateTo RC1 -exportXMP
%RealityCapture% -delegateTo RC1 -selectMaximalComponent
%RealityCapture% -delegateTo RC1 -exportSelectedComponentDir "%output_dir%"

if defined GENERATE_MODEL_BOOL (
    %RealityCapture% -delegateTo RC1 -calculateHighModel

    if defined CULL_POLYGONS_BOOL (
        %RealityCapture% -delegateTo RC1 -cleanModel
        %RealityCapture% -delegateTo RC1 -selectLargeTrianglesRel 20
        %RealityCapture% -delegateTo RC1 -removeSelectedTriangles
        %RealityCapture% -delegateTo RC1 -cleanModel
        %RealityCapture% -delegateTo RC1 -selectLargestModelComponent
        %RealityCapture% -delegateTo RC1 -invertTrianglesSelection
        %RealityCapture% -delegateTo RC1 -removeSelectedTriangles
        %RealityCapture% -delegateTo RC1 -cleanModel
        %RealityCapture% -delegateTo RC1 -calculateVertexColors
    )

    %RealityCapture% -delegateTo RC1 -save "%output_dir%\\%scene_name%.rcproj"
)

%RealityCapture% -delegateTo RC1 -quit