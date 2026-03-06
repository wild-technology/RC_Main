These scripts were created by Epic Games Slovakia, who doesn't carry any liability in case issues related to the sample occur.
These scripts can be used to align images, create and export textured 3D model.

All you need to do is:

1. change the path to the source folders in the SetVariables.bat as follows:

RealityCaptureExe - path to the installation folder where RealityCapture.exe is stored
RootFolder - path to all source folders (where this README.txt file is located)

2. run _ProcessAll.bat (doubleclick)

DESCRIPTION OF A BATCH FILE
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
_ProcessAll.bat 

In this batch file the actual processing inside RealityCapture is defined.
This batch file contains:

Setting the variables (if the original data structure and naming is preserved, no need to change them):

This batch file contains:

	Images		A path to the folder which contains images.
	ModelName	A new name for the created model.
	Model		A path where model is going to be exported.
	Project		A path where project is going to be stored.

Processing in RealityCapute:
- load images
- align
- set reconstruction region automatically
- calculate normal detail model
- select marginal triangles
- remove marginal triangles
- rename selected model
- calculate texture (and unwrap)
- save project
- export model
- quit