:: These scripts were created by Epic Games Slovakia, who doesn't carry any liability in case issues related to the sample occur.

:: Switch on/off console output.
@echo off
REM setlocal EnableDelayedExpansion

:: Path to RealityCapture application.
set RealityCapture="C:\Program Files\Capturing Reality\RealityCapture\RealityCapture.exe" 

:: Root path to work folders where all the datasets are stored 
set RootFolder=%~dp0\\..\\

:: Variable storing path to working directory
set workingDir=%~dp0

:: A path to the metadata folder.
set Metadata=%RootFolder%\Metadata

:: A path to the models folder.
set Models=%RootFolder%\Models
::creates folder "Models" if it does not exists
if not exist "%Models%" mkdir "%Models%"

:: A path to the Error folder.
set ErrorPath=%RootFolder%\Errors
::creates folder "Models" if it does not exists
if not exist "%Errors%" mkdir "%Errors%"

:: Variable storing name of file with Error write script.
set ErrorWriter=%ErrorPath%\ErrorWriter.bat

:: Variable storing path to xmp metadata.
set XMPMetadata=%Metadata%\xmp

:: Variable storing name of file with parameters for Alignment settings
set AlignParams=%Metadata%\AlignmentParams.xml

:: Variable storing name of file with parameters for exporting model to .* file format.
set ModelExportParams=%Metadata%\ModelExportParams.xml

:: Variable storing name of file with parameters for exporting model to .glb file format.
set ModelExportParamsGLB=%Metadata%\ModelExportParamsGLB.xml

:: Variable storing name of file with parameters for exporting model to .obj file format.
set ModelExportParamsOBJ=%Metadata%\ModelExportParamsOBJ.xml

:: Variable storing name of file with parameters for exporting model to .fbx file format with U1_V1 tile type.
set ModelExportParamsFBXU1V1=%Metadata%\ModelExportParamsFBX_U1V1.xml

:: Variable storing name of file with parameters for exporting model to .fbx file format with U1_V1 tile type.
set ModelExportParamsFBXU1V1Material=%Metadata%\ModelExportParamsFBX_U1V1_material.xml

:: Variable storing name of file with parameters for exporting model to .fbx file format with U_V tile type.
set ModelExportParamsFBXUV=%Metadata%\ModelExportParamsFBX_UV.xml

:: Variable storing name of file with parameters for exporting model to .fbx file format with UDIM tile type and material creation OFF.
set ModelExportParamsFBXUDIM=%Metadata%\ModelExportParamsFBX_UDIM.xml

:: Variable storing name of file with parameters for exporting model to .fbx file format with UDIM tile type and material creation ON.
set ModelExportParamsFBXUDIMMaterial=%Metadata%\ModelExportParamsFBX_UDIM_material.xml

:: Variable storing name of file with parameters for texturing (MaxTextureCount1 8K UV unwrap)
set Texturing1x8k=%Metadata%\Texturing_MaxTextureCount1_8k.xml

:: Variable storing name of file with parameters for texturing (MaxTextureCount4 8K UV unwrap)
set Texturing4x8k=%Metadata%\Texturing_MaxTextureCount4_8k.xml

:: Variable storing name of file with parameters for texturing (MaxTextureCount1 16K UV unwrap)
set Texturing1x16k=%Metadata%\Texturing_MaxTextureCount1_16k.xml

:: Variable storing name of file with parameters for texturing (Fixed texel size 50% quality UV unwrap)
set TexturingFixedTexSize50=%Metadata%\Texturing_FixedTexelSize50perQuality.xml

:: Variable storing name of file with parameters for texturing (Fixed texel size 100% quality UV unwrap)
set TexturingFixedTexSize100=%Metadata%\Texturing_FixedTexelSize100perQuality.xml

:: Variable storing name of file with parameters for texture reprojection
set ReprojectionParams=%Metadata%\ReprojectionParams.xml

:: Variable storing name of file with parameters for simplification to 500k
set Simplify500k=%Metadata%\Simplify500k_Params.xml

:: Variable storing name of file with parameters for simplification by 50%
set Simplify50per=%Metadata%\Simplify50Per_Params.xml

:: Variable storing name of file with parameters for smoothing to 0.2 and 2 iterations
set SmoothingParams=%Metadata%\Smoothing_02_2_Params.xml

::set variable "reconRegion" for counting files in
set ReconRegion=%RootFolder%\ReconRegion