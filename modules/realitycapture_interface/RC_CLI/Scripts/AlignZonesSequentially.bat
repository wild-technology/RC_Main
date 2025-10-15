@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo RealityCapture Zone Alignment Script
echo ============================================================
echo.

rem Read default variables
echo [1/10] Reading default variables...
call "%~dp0SetVariables.bat"
if errorlevel 1 (
    echo ERROR: Failed to load SetVariables.bat
    exit /b 1
)

set RootFolder=%~dp0..\
set MetadataDir=%RootFolder%Metadata
set AlignmentParams=%MetadataDir%\AlignmentParams.xml
set FlightLogParams=%MetadataDir%\FlightLogParams.xml
set ErrorPath=%RootFolder%Errors
set ErrorWriter=%ErrorPath%\ErrorWriter.bat

rem Validate metadata files exist
if not exist "%AlignmentParams%" (
    echo ERROR: AlignmentParams.xml not found at: %AlignmentParams%
    exit /b 1
)
if not exist "%FlightLogParams%" (
    echo ERROR: FlightLogParams.xml not found at: %FlightLogParams%
    exit /b 1
)
echo    SUCCESS: Metadata files validated
echo.

rem Parse input arguments
echo [2/10] Parsing input arguments...
if [%1] == [] (
    set /P zone_input="Zone Input Directory: "
) else (
    set zone_input=%~1
)

if [%2] == [] (
    set /P zone_output="Zone Output Directory: "
) else (
    set zone_output=%~2
)

rem Validate input directory exists
if not exist "%zone_input%" (
    echo ERROR: Zone input directory does not exist: %zone_input%
    exit /b 1
)

rem Count images in input directory
set image_count=0
for /r "%zone_input%" %%F in (*.jpg *.jpeg *.png *.heif) do (
    set /a image_count+=1
)
if %image_count% == 0 (
    echo ERROR: No images found in input directory: %zone_input%
    exit /b 1
)

echo    Zone Input: %zone_input%
echo    Zone Output: %zone_output%
echo    Images Found: %image_count%
echo    SUCCESS: Input validated
echo.

rem Create required directories
echo [3/10] Creating required directories...
if not exist "%ErrorPath%" (
    mkdir "%ErrorPath%"
    echo    Created error directory: %ErrorPath%
)

rem Create output directory
if not exist "%zone_output%" (
    mkdir "%zone_output%"
    if errorlevel 1 (
        echo ERROR: Failed to create output directory: %zone_output%
        exit /b 1
    )
    echo    SUCCESS: Created %zone_output%
) else (
    echo    INFO: Output directory already exists
)
echo.

rem Start RealityCapture
echo [4/10] Starting RealityCapture...
call startRealityCapture.bat
if errorlevel 1 (
    echo ERROR: Failed to start RealityCapture
    exit /b 1
)
echo    SUCCESS: RealityCapture started
echo.

rem Create new project
echo [5/10] Creating new project...
%RealityCapture% -newScene
if errorlevel 1 (
    echo ERROR: Failed to create new scene
    %RealityCapture% -quit
    exit /b 1
)
echo    SUCCESS: New scene created
echo.

rem Add images from zone folder
echo [6/10] Adding images from zone folder...
echo    This may take several minutes for large image sets...
%RealityCapture% -addFolder "%zone_input%"
if errorlevel 1 (
    echo ERROR: Failed to add images from folder
    %RealityCapture% -quit
    exit /b 1
)
echo    SUCCESS: Images added from %zone_input%
echo.

rem Find and import flight log
echo [7/10] Importing flight log...
set flight_log_found=0
for %%F in (%zone_input%\flight_log*.txt) do (
    echo    Importing: %%F
    %RealityCapture% -importFlightLog "%%F" "%FlightLogParams%"
    if errorlevel 1 (
        echo ERROR: Failed to import flight log: %%F
        %RealityCapture% -quit
        exit /b 1
    ) else (
        echo    SUCCESS: Flight log imported
        set flight_log_found=1
    )
)
if %flight_log_found% == 0 (
    echo ERROR: No flight log found in %zone_input%
    echo    Flight log is REQUIRED for georeferenced alignment
    %RealityCapture% -quit
    exit /b 1
)
echo.

rem Verify XMP sidecars exist
echo [8/10] Verifying XMP sidecars for camera calibration...
set xmp_count=0
for /r "%zone_input%" %%F in (*.xmp) do (
    set /a xmp_count+=1
)
if %xmp_count% == 0 (
    echo ERROR: No XMP sidecar files found in %zone_input%
    echo    XMP files are REQUIRED for camera calibration priors
    %RealityCapture% -quit
    exit /b 1
)
echo    Found %xmp_count% XMP files

rem Import XMP sidecars with calibration priors
echo    Importing XMP sidecars...
%RealityCapture% -importXMP
if errorlevel 1 (
    echo ERROR: Failed to import XMP sidecars
    echo    Camera calibration priors are REQUIRED
    %RealityCapture% -quit
    exit /b 1
)
echo    SUCCESS: XMP sidecars imported with camera priors
echo.

rem Run alignment
echo [9/10] Running alignment with custom parameters...
echo    This may take a significant amount of time...
echo    Please wait...
%RealityCapture% -align %AlignmentParams%
if errorlevel 1 (
    echo ERROR: Alignment failed
    echo    Check RealityCapture window for details
    %RealityCapture% -quit
    exit /b 1
)
echo    SUCCESS: Alignment completed
echo.

rem Export components
echo [10/10] Exporting aligned components...
%RealityCapture% -selectAllComponents
if errorlevel 1 (
    echo ERROR: Failed to select components
    %RealityCapture% -quit
    exit /b 1
)

%RealityCapture% -exportSelectedComponentDir "%zone_output%"
if errorlevel 1 (
    echo WARNING: Failed to export components
    echo    Project will still be saved
) else (
    echo    SUCCESS: Components exported to %zone_output%
)
echo.

rem Save project
echo Saving project...
for %%Z in (%zone_input%) do set zone_name=%%~nxZ
%RealityCapture% -save "%zone_input%\%zone_name%.rcproj"
if errorlevel 1 (
    echo ERROR: Failed to save project
    %RealityCapture% -quit
    exit /b 1
)
echo    SUCCESS: Project saved as %zone_input%\%zone_name%.rcproj
echo.

echo Closing RealityCapture...
%RealityCapture% -quit

echo ============================================================
echo PROCESSING COMPLETE
echo ============================================================
echo Zone: %zone_name%
echo Components: %zone_output%
echo Project: %zone_input%\%zone_name%.rcproj
echo ============================================================
exit /b 0