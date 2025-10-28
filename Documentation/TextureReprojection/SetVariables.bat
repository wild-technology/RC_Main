::CapturingReality

:: switch off console output
@echo off

:: path to RealityCapture application
set RealityCaptureExe="C:\Program Files\Capturing Reality\RealityCapture\RealityCapture.exe"

:: root path to work folders where all the datasets are stored (%~dp0 means the flder in which this script is stored)
set RootFolder=%~dp0