:: Process-completion hook invoked by RealityScan itself (appProcessAction=
:: ExecuteProgram / appProcessExecCmd). Arguments:
::   %1 = $(processResult)      result code of the finished process
::   %2 = $(processId)          process id
::   %3 = $(processDuration:d)  duration in seconds
::   %4 = errors folder path
::
:: Every completion is appended to results.log so the orchestrator has an
:: event-driven record of finished operations. Result codes other than 0
:: and 1 are treated as failures and appended to errors.txt, which makes
:: the workflow scripts abort at their next synchronisation point.
@echo off
echo %date% %time% process %2 finished with result code %1 in %3 seconds >> "%~4\results.log"
if /i "%1" NEQ "0" (
    if /i "%1" NEQ "1" (
        echo An error occurred: process %2 finished with result code %1 in %3 seconds. >> "%~4\errors.txt"
    )
)
