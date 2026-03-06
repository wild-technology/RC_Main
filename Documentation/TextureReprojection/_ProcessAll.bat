::CapturingReality

:: switch off console output
@echo off

call SetVariables.bat

:: set path to the folder with your images
set Images="%RootFolder%\Images"

:: set the name for the high-poly model
set HighPoly=HighPoly

:: set the name for the low-poly model
set LowPoly=LowPoly

:: set the value of triangles to which high-poly model will be simplified
set SimplifyTo=10000

:: set the path to the .xml with Texture Reprjection settings
set TextureReproSettings="%RootFolder%\TextureReprojectionSettings.xml"

:: set the path, where model is going to be saved, and its name
set Model="%RootFolder%\LowPolyModel.obj"

:: set the path, where project is going to be saved, and its name
set Project="%RootFolder%\Project.rcproj"

:: run RealityCapture
%RealityCaptureExe% -newScene ^
        -addFolder %Images% ^
        -align ^
        -selectMaximalComponent ^
        -setReconstructionRegionAuto ^
        -calculateNormalModel ^
        -renameSelectedModel %HighPoly% ^
        -calculateTexture ^
        -simplify %SimplifyTo% ^
        -renameSelectedModel %LowPoly% ^
        -unwrap ^
        -reprojectTexture %HighPoly% %LowPoly% %TextureReproSettings% ^
        -selectModel %LowPoly% ^
        -exportModel %LowPoly% %Model% ^
        -save %Project% ^
        -quit
