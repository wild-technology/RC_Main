These scripts were created by Epic Games Slovakia, who doesn't carry any liability in case issues related to the sample occur.
These scripts can be used to create a model, which is then simplified and has its texture reprojected on the simplified model.
You can download any sample dataset that can be used to run this script here, for example "Head Scan"
https://www.capturingreality.com/SampleDatasets


All you need to do is:

1. change the path to the source folders in the SetVariables.bat as follows:

RealityCaptureExe - path to the installation folder where RealityCapture.exe is stored
RootFolder - path to all source folders (where this README.txt file is located)

2. run _ProcessAll.bat (doubleclick)

DESCRIPTION OF BATCH FILEs
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
SetVariables.bat 

Setting the path to the RealityCapture application and the path to the folder with source data.
This batch file is called inside the ProcessDataSet.bat.

- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
ProcessDataSet.bat 

In this batch file the actual processing inside RealityCapture is defined.
This batch file contains:

Setting the variables (if the original data structure and naming is preserved, no need to change them):

   Images         A path to the folder which contains images.
   HighPoly	  Set the name for the model with high amount of triangles.
   LowPoly	  Set the name for the model with low amount of triangles (simplified model).
   SimplifyTo     A value of triangles to which a created model is going to be simplified.
   Model	  A path where model is going to be exported (with its name and format).
   Project	  A path where project is going to be saved (with its name and format - .rcproj).

Processing in RealityCapute:
- load images
- align
- select a component with the most amount of cameras in it (in case more components were created)
- automatically create a reconstruction region based on the registration
- reconstruction in normal detail
- rename created model
- texture created model (this will also create unwrap)
- simplify created model to a specified number of triangles
- rename simplified model
- create unwrap for simplified model
- reproject texture onto a simplified model (including creatin normal and disaplcement maps)
- select simplified model
- export simplified model
- save project
- quit
