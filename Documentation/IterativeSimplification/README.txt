These scripts were created by Epic Games Slovakia, who doesn't carry any liability in case issues related to the sample occur. 

In this folder you can find a set of batch files used to simplify your model by 50% in every iteration. The Simplification batch file is meant to be used on the running project with one model selected.

All you need to do is:

1. Change the path to the source folders in the Scripts\SetVariables.bat as follows:

	RealityCaptureExe - path to the installation folder where RealityCapture.exe is stored,
	RootFolder - path to all source folders (where this README.txt file is located). No need to change the path if you preserve the original structure of the files and folders.

2. Run Simplification.bat (doubleclick)

DESCRIPTION OF BATCH FILEs
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
SetVariables.bat 

Setting the path to the RealityCapture application and the path to the folder with source data.
This batch file is called inside the Simplification.bat.

- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
Simplification.bat

In this batch file the simplification command is delegated to an open instance of RealityCapture several times. The number of times is defined by the Iterations variable.

Setting the variables (if the original data structure and naming is preserved, no need to change them):

   SimplificationParameters			A path to the simplification parameters which can be obtained in RealityCapture. You can export them and use them based on your needs.
   Iterations					This number indicates how many times is simplification going to be executed with the same parameters.

Processing in CMD
- FOR cycle that loops through a range of numbers. Numbers in brackets define start,step,end values, therefore the end value is %Iterations%.

Processing in RealityCapture:
- finding an open instance of RealityCapture (project with one selected model is expected)
- simplify.



