@echo off

echo Reading default variables
call SetVariables.bat

set RootFolder=%~dp0\\..\\
set MetadataDir=%RootFolder%\Metadata
set HighModelTexture=%MetadataDir%\Texturing_HighPolyTexture.xml
set SimplifiedModelTexture=%MetadataDir%\Texturing_SimplifiedPolyTexture.xml
set SimplifyParams=%MetadataDir%\SimplifyAutomationParams.xml
set UnwrapSimplified=%MetadataDir%\Unwrapping_Simplified.xml

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

    if [%8] == [] (
        CHOICE /C YN /M "Texture (Y/N):"
        set texture_model=%ERRORLEVEL%
    ) else (
        set texture_model=%~6
    )

    if [%8] == [] (
        CHOICE /C YN /M "Simplify (Y/N):"
        set simplify_model=%ERRORLEVEL%
    ) else (
        set simplify_model=%~6
    )
)

echo Input Directory: %input_dir%
echo Output Directory: %output_dir%
echo Flight Log Directory: %flight_log_dir%
echo Flight Log Params Directory: %flight_log_params_dir%
echo Generate Model: %generate_model%
echo Cull Polygons: %cull_polygons%
echo Scene Name: %scene_name%

set "CULL_POLYGONS_BOOL="
if [%cull_polygons%] == [Y] set CULL_POLYGONS_BOOL=1
if [%cull_polygons%] == [true] set CULL_POLYGONS_BOOL=1

set "TEXTURE_MODEL_BOOL="
if [%texture_model%] == [Y] set TEXTURE_MODEL_BOOL=1
if [%texture_model%] == [true] set TEXTURE_MODEL_BOOL=1

set "SIMPLIFY_MODEL_BOOL="
if [%simplify_model%] == [Y] set SIMPLIFY_MODEL_BOOL=1
if [%simplify_model%] == [true] set SIMPLIFY_MODEL_BOOL=1

echo Starting Reality Capture
call startRealityCapture.bat

echo Adding images to project
%RealityCapture% -delegateTo RC1 -addFolder "%input_dir%"

if not [flight_log_dir] == [] (
    %RealityCapture% -delegateTo RC1 -importFlightLog "%flight_log_dir%" "%flight_log_params_dir%"
)

echo Aligning images
%RealityCapture% -delegateTo RC1 -align
%RealityCapture% -delegateTo RC1 -exportXMP

echo Selecting maximal component and exporting
%RealityCapture% -delegateTo RC1 -mergeComponents
%RealityCapture% -delegateTo RC1 -renameSelectedComponent "Merged"
%RealityCapture% -delegateTo RC1 -exportSelectedComponentDir "%output_dir%"

if defined GENERATE_MODEL_BOOL (
    echo Generating model
    %RealityCapture% -delegateTo RC1 -calculateHighModel
    %RealityCapture% -delegateTo RC1 -renameSelectedModel "HighPoly"

    if defined CULL_POLYGONS_BOOL (
        echo Culling polygons
        %RealityCapture% -delegateTo RC1 -cleanModel
        %RealityCapture% -delegateTo RC1 -renameSelectedModel "CullTemp1"

        %RealityCapture% -delegateTo RC1 -selectLargeTrianglesRel 20
        %RealityCapture% -delegateTo RC1 -removeSelectedTriangles
        %RealityCapture% -delegateTo RC1 -renameSelectedModel "CullTemp2"

        %RealityCapture% -delegateTo RC1 -cleanModel
        %RealityCapture% -delegateTo RC1 -renameSelectedModel "Culled"

        %RealityCapture% -delegateTo RC1 -selectModel "CullTemp1"
        %RealityCapture% -delegateTo RC1 -deleteSelectedModel

        %RealityCapture% -delegateTo RC1 -selectModel "CullTemp2"
        %RealityCapture% -delegateTo RC1 -deleteSelectedModel 

        %RealityCapture% -delegateTo RC1 -selectModel "Culled"
    )

    if defined TEXTURE_MODEL_BOOL (
        echo Texturing model
        %RealityCapture% -delegateTo RC1 -calculateTexture %HighModelTexture%
        %RealityCapture% -delegateTo RC1 -renameSelectedModel "HighPolyTextured"
    )

    if defined SIMPLIFY_MODEL_BOOL (
        echo Simplifying model
        %RealityCapture% -delegateTo RC1 -simplify %SimplifyParams%
        %RealityCapture% -delegateTo RC1 -renameSelectedModel "SimplifyTemp1"
        %RealityCapture% -delegateTo RC1 -cleanModel
        %RealityCapture% -delegateTo RC1 -renameSelectedModel "SimplifyTemp2"
        %RealityCapture% -delegateTo RC1 -simplify %SimplifyParams%
        %RealityCapture% -delegateTo RC1 -renameSelectedModel "SimplifyTemp3"
        %RealityCapture% -delegateTo RC1 -cleanModel
        %RealityCapture% -delegateTo RC1 -renameSelectedModel "SimplifyTemp4"
        %RealityCapture% -delegateTo RC1 -simplify %SimplifyParams%
        %RealityCapture% -delegateTo RC1 -renameSelectedModel "SimplifyTemp5"
        %RealityCapture% -delegateTo RC1 -cleanModel
        %RealityCapture% -delegateTo RC1 -renameSelectedModel "SimplifyTemp6"
        %RealityCapture% -delegateTo RC1 -simplify %SimplifyParams%
        %RealityCapture% -delegateTo RC1 -renameSelectedModel "SimplifyTemp7"
        %RealityCapture% -delegateTo RC1 -cleanModel
        %RealityCapture% -delegateTo RC1 -renameSelectedModel "Simplified"

        %RealityCapture% -delegateTo RC1 -selectModel "SimplifyTemp1"
        %RealityCapture% -delegateTo RC1 -deleteSelectedModel

        %RealityCapture% -delegateTo RC1 -selectModel "SimplifyTemp2"
        %RealityCapture% -delegateTo RC1 -deleteSelectedModel

        %RealityCapture% -delegateTo RC1 -selectModel "SimplifyTemp3"
        %RealityCapture% -delegateTo RC1 -deleteSelectedModel

        %RealityCapture% -delegateTo RC1 -selectModel "SimplifyTemp4"
        %RealityCapture% -delegateTo RC1 -deleteSelectedModel

        %RealityCapture% -delegateTo RC1 -selectModel "SimplifyTemp5"
        %RealityCapture% -delegateTo RC1 -deleteSelectedModel

        %RealityCapture% -delegateTo RC1 -selectModel "SimplifyTemp6"
        %RealityCapture% -delegateTo RC1 -deleteSelectedModel

        %RealityCapture% -delegateTo RC1 -selectModel "SimplifyTemp7"
        %RealityCapture% -delegateTo RC1 -deleteSelectedModel

        if defined TEXTURE_MODEL_BOOL (
            echo Unwrapping simplified model
            %RealityCapture% -delegateTo RC1 -selectModel "Simplified"
            %RealityCapture% -delegateTo RC1 -unwrap %UnwrapSimplified%

            echo Reprojecting onto simplified model
            %RealityCapture% -delegateTo RC1 -reprojectTexture "HighPolyTextured" "Simplified"
            %RealityCapture% -delegateTo RC1 -renameSelectedModel "SimplifiedTextured"
        )
    )
)

echo Saving project
%RealityCapture% -delegateTo RC1 -save "%output_dir%\\%scene_name%.rcproj"