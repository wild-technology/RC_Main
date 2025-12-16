::CapturingReality

:: switch off console output
@echo off

call SetVariables.bat

:: variable storing path to images for creating model
set Images="%RootFolder%\Images"

:: set a new name for calculated model
set ModelName="SmallPlastic"

:: set the path, where model is going to be saved, and its name
set Model="%RootFolder%\SmallPlastic.obj"

:: variable storing path to images for texturing model
set Project="%RootFolder%\Smallplastic.rcproj"

:: run RealityCapture
%RealityCaptureExe% -addFolder %Images% ^
        -align ^
        -setReconstructionRegionAuto ^
        -calculateNormalModel ^
        -selectMarginalTriangles ^
        -removeSelectedTriangles ^
        -renameSelectedModel %ModelName% ^
        -calculateTexture ^
        -save %Project% ^
        -exportModel %ModelName% %Model% ^
        -quit
       
        

        





