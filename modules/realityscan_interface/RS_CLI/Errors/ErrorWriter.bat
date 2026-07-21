:: Process-completion hook invoked by RealityScan itself (appProcessAction=
:: ExecuteProgram / appProcessExecCmd). Arguments:
::   %1 = $(processResult)      result code of the finished process
::   %2 = $(processId)          process id
::   %3 = $(processDuration:d)  duration in seconds
::   %4 = instance name (used to namespace the marker files so parallel
::        instances never read each other's state)
::
:: Marker files are written next to this script (%~dp0), so no path with
:: spaces ever has to survive the appProcessExecCmd command line.
::
:: Every completion is appended to results_<instance>.log so the
:: orchestrator has an event-driven record of finished operations. Result
:: codes other than 0 and 1 are treated as failures and appended to
:: errors_<instance>.txt, which makes the workflow scripts abort at their
:: next synchronisation point. (Code 1 is whitelisted per the original
:: Epic sample scripts.)
@echo off
set "instance=%~4"
if "%instance%" == "" set "instance=RS1"
echo %date% %time% process %2 finished with result code %1 in %3 seconds >> "%~dp0results_%instance%.log"
if /i "%1" NEQ "0" (
    if /i "%1" NEQ "1" (
        echo An error occurred: process %2 finished with result code %1 in %3 seconds. >> "%~dp0errors_%instance%.txt"
    )
)
