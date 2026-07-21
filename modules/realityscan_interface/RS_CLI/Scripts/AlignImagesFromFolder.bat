@echo off
:: Align a folder of images in RealityScan and optionally generate, cull,
:: texture, and simplify the model.
::
:: Arguments (all optional; prompted for when omitted):
::   %1 input image directory        %2 component output directory
::   %3 flight log path (or "")      %4 flight log params xml (or "")
::   %5 generate model (true/false)  %6 cull polygons (true/false)
::   %7 scene/component name         %8 texture model (true/false)
::   %9 simplify model (true/false)
::
:: Every RealityScan operation goes through the :run subroutine, which
:: delegates to the %RS_INSTANCE% headless instance, waits for completion,
:: and aborts the workflow if RealityScan's process trigger reported an
:: error (see startRealityScan.bat and ErrorWriter.bat).

echo Reading default variables
call "%~dp0SetVariables.bat"
if errorlevel 1 exit /b 1
set MetadataDir=%Metadata%
set HighModelTexture=%MetadataDir%\Texturing_HighPolyTexture.xml
set SimplifiedModelTexture=%MetadataDir%\Texturing_SimplifiedTexture.xml
set SimplifyParams=%MetadataDir%\SimplifyAutomationParams.xml
set UnwrapSimplified=%MetadataDir%\Unwrapping_Simplified.xml

echo Setting workflow variables

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

if not "%flight_log_dir%" == "" if [%4] == [] (
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
if [%generate_model%] == [1] set GENERATE_MODEL_BOOL=1
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
        set texture_model=%~8
    )

    if [%9] == [] (
        CHOICE /C YN /M "Simplify (Y/N):"
        set simplify_model=%ERRORLEVEL%
    ) else (
        set simplify_model=%~9
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
if [%cull_polygons%] == [1] set CULL_POLYGONS_BOOL=1
if [%cull_polygons%] == [Y] set CULL_POLYGONS_BOOL=1
if [%cull_polygons%] == [true] set CULL_POLYGONS_BOOL=1

set "TEXTURE_MODEL_BOOL="
if [%texture_model%] == [1] set TEXTURE_MODEL_BOOL=1
if [%texture_model%] == [Y] set TEXTURE_MODEL_BOOL=1
if [%texture_model%] == [true] set TEXTURE_MODEL_BOOL=1

set "SIMPLIFY_MODEL_BOOL="
if [%simplify_model%] == [1] set SIMPLIFY_MODEL_BOOL=1
if [%simplify_model%] == [Y] set SIMPLIFY_MODEL_BOOL=1
if [%simplify_model%] == [true] set SIMPLIFY_MODEL_BOOL=1

echo Starting RealityScan
call "%~dp0startRealityScan.bat"
if errorlevel 1 exit /b 1

echo Adding images to project
call :run -addFolder "%input_dir%" || goto :fail

if not "%flight_log_dir%" == "" (
    call :run -importFlightLog "%flight_log_dir%" "%flight_log_params_dir%" || goto :fail
)

echo Aligning images
call :run -align || goto :fail
call :run -exportXMP || goto :fail

echo Selecting maximal component and exporting
call :run -mergeComponents || goto :fail
call :run -renameSelectedComponent "Merged" || goto :fail
call :run -exportSelectedComponentDir "%output_dir%" || goto :fail

if not defined GENERATE_MODEL_BOOL goto :saveProject

echo Generating model
call :run -calculateHighModel || goto :fail
call :run -renameSelectedModel "HighPoly" || goto :fail

if defined CULL_POLYGONS_BOOL (
    echo Culling polygons
    call :run -cleanModel || goto :fail
    call :run -renameSelectedModel "CullTemp1" || goto :fail

    call :run -selectLargeTrianglesRel 20 || goto :fail
    call :run -removeSelectedTriangles || goto :fail
    call :run -renameSelectedModel "CullTemp2" || goto :fail

    call :run -cleanModel || goto :fail
    call :run -renameSelectedModel "Culled" || goto :fail

    call :run -selectModel "CullTemp1" || goto :fail
    call :run -deleteSelectedModel || goto :fail

    call :run -selectModel "CullTemp2" || goto :fail
    call :run -deleteSelectedModel || goto :fail

    call :run -selectModel "Culled" || goto :fail
)

if defined TEXTURE_MODEL_BOOL (
    echo Texturing model
    call :run -calculateTexture "%HighModelTexture%" || goto :fail
    call :run -renameSelectedModel "HighPolyTextured" || goto :fail
)

if not defined SIMPLIFY_MODEL_BOOL goto :saveProject

echo Simplifying model - four simplify/clean passes
for /L %%I in (1,1,3) do (
    call :run -simplify "%SimplifyParams%" || goto :fail
    call :run -renameSelectedModel "SimplifyPass%%IRaw" || goto :fail
    call :run -cleanModel || goto :fail
    call :run -renameSelectedModel "SimplifyPass%%IClean" || goto :fail
)
call :run -simplify "%SimplifyParams%" || goto :fail
call :run -renameSelectedModel "SimplifyPass4Raw" || goto :fail
call :run -cleanModel || goto :fail
call :run -renameSelectedModel "Simplified" || goto :fail

echo Deleting intermediate simplification models
for /L %%I in (1,1,3) do (
    call :run -selectModel "SimplifyPass%%IRaw" || goto :fail
    call :run -deleteSelectedModel || goto :fail
    call :run -selectModel "SimplifyPass%%IClean" || goto :fail
    call :run -deleteSelectedModel || goto :fail
)
call :run -selectModel "SimplifyPass4Raw" || goto :fail
call :run -deleteSelectedModel || goto :fail

if defined TEXTURE_MODEL_BOOL (
    echo Unwrapping simplified model
    call :run -selectModel "Simplified" || goto :fail
    call :run -unwrap "%UnwrapSimplified%" || goto :fail

    echo Reprojecting onto simplified model
    call :run -reprojectTexture "HighPolyTextured" "Simplified" || goto :fail
    call :run -renameSelectedModel "SimplifiedTextured" || goto :fail
)

:saveProject
echo Saving project
call :run -save "%output_dir%\%scene_name%.rsproj" || goto :fail

echo Shutting down RealityScan instance %RS_INSTANCE%
%RealityScan% -delegateTo %RS_INSTANCE% -quit
exit /b 0

:fail
echo ERROR: Workflow failed - see %ErrorPath%\errors.txt and the RealityScan log
%RealityScan% -delegateTo %RS_INSTANCE% -quit
exit /b 1

:: ------------------------------------------------------------------
:: :run <command...> - delegate one operation to %RS_INSTANCE%, wait for it
:: to finish, and fail if RealityScan reported an error.
::
:: -waitCompleted is issued twice with a grace period because it can return
:: prematurely when called before the instance has picked up the queued
:: command. errors.txt is written by RealityScan's own process trigger
:: (appProcessExecCmd -> ErrorWriter.bat), so a non-empty file means an
:: operation genuinely failed even if the delegating call returned 0.
:: ------------------------------------------------------------------
:run
%RealityScan% -delegateTo %RS_INSTANCE% %*
if errorlevel 1 (
    echo ERROR: Failed to delegate command: %*
    exit /b 1
)
ping -n 2 127.0.0.1 >nul
%RealityScan% -waitCompleted %RS_INSTANCE%
ping -n 2 127.0.0.1 >nul
%RealityScan% -waitCompleted %RS_INSTANCE%
if exist "%ErrorPath%\errors.txt" (
    for %%A in ("%ErrorPath%\errors.txt") do if %%~zA GTR 0 (
        echo ERROR: RealityScan reported a failure during: %*
        exit /b 1
    )
)
exit /b 0
