@echo off

echo Reading default variables
set RootFolder=%~dp0..\
set MetadataDir=%RootFolder%Metadata
call SetVariables.bat
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
    set /P zone_name="Zone Name (for project file): "
) else (
    set zone_name=%~3
)

if [%4] == [] (
    set /P flight_log_dir="Path to Flight Log (or empty if no flight log): "
) else (
    set flight_log_dir=%~4
)

if not [flight_log_dir] == [] if [%5] == [] (
    set /P flight_log_params_dir="Path to Flight Log Params: "
) else (
    set flight_log_params_dir=%~5
)

if [%6] == [] (
    CHOICE /C YN /M "Generate Model (Y/N):"
    set generate_model=%ERRORLEVEL%
) else (
    set generate_model=%~6
)

set "GENERATE_MODEL_BOOL="
if [%generate_model%] == [Y] set GENERATE_MODEL_BOOL=1
if [%generate_model%] == [true] set GENERATE_MODEL_BOOL=1

if defined GENERATE_MODEL_BOOL (
    if [%7] == [] (
        CHOICE /C YN /M "Cull Polygons (Y/N):"
        set cull_polygons=%ERRORLEVEL%
    ) else (
        set cull_polygons=%~7
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
if [%cull_polygons%] == [Y] set CULL_POLYGONS_BOOL=1
if [%cull_polygons%] == [true] set CULL_POLYGONS_BOOL=1

set "TEXTURE_MODEL_BOOL="
if [%texture_model%] == [Y] set TEXTURE_MODEL_BOOL=1
if [%texture_model%] == [true] set TEXTURE_MODEL_BOOL=1

set "SIMPLIFY_MODEL_BOOL="
if [%simplify_model%] == [Y] set SIMPLIFY_MODEL_BOOL=1
if [%simplify_model%] == [true] set SIMPLIFY_MODEL_BOOL=1

REM Kill any existing RealityCapture/RealityScan instances
echo Checking for existing RealityScan instances
tasklist /FI "IMAGENAME eq RealityScan.exe" 2>NUL | find /I /N "RealityScan.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo Killing existing RealityScan instance
    taskkill /F /IM RealityScan.exe >NUL 2>&1
    timeout /t 3 /nobreak >NUL
)

echo ========================================
echo Starting RealityScan 2.0 Processing
echo ========================================
echo Input: %input_dir%
echo Output: %output_dir%
echo Flight log: %flight_log_dir%
echo.

REM Execute RealityScan with command sequence
REM Per official docs, commands are chained on single line
REM NOTE: RealityScan 2.0 automatically imports XMP sidecar files via -addFolder
REM      No separate -importXMP command is needed (it causes "unknown command" error)
REM IMPORTANT: Use -set "appIncSubdirs=true" to import from camera subfolders (per official docs)

REM Build command in two steps to avoid batch parsing issues with nested quotes
if not [%flight_log_dir%] == [] goto :WITH_FLIGHTLOG

:WITHOUT_FLIGHTLOG
echo Processing with XMP calibration priors (no flight log)...
"%RealityCapture%" -newScene -set "appIncSubdirs=true" -set "appUseRelativeImagePaths=false" -addFolder "%input_dir%" -align -exportXMP -selectMaximalComponent -exportSelectedComponentDir "%output_dir%" -save "%input_dir%\%zone_name%.rcproj" -quit
goto :END_PROCESSING

:WITH_FLIGHTLOG
echo Processing with flight log and XMP calibration priors...
"%RealityCapture%" -newScene -set "appIncSubdirs=true" -set "appUseRelativeImagePaths=false" -addFolder "%input_dir%" -importFlightLog "%flight_log_dir%" "%flight_log_params_dir%" -align -exportXMP -selectMaximalComponent -exportSelectedComponentDir "%output_dir%" -save "%input_dir%\%zone_name%.rcproj" -quit

:END_PROCESSING

if ERRORLEVEL 1 (
    echo ERROR: RealityScan processing failed with exit code %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)

echo ========================================
echo ALL STEPS COMPLETE
echo ========================================
echo Components exported to: %output_dir%
echo Project saved to: %input_dir%\%zone_name%.rcproj
exit /b 0

REM TODO: Add model generation support in future version
REM Model generation commands need to be added to the command sequence above
REM Example: -calculateHighModel -cleanModel -calculateTexture params.xml