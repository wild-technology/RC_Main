:: These scripts were created by Epic Games Slovakia, who doesn't carry any liability in case issues related to the sample occur.

:: ErrorWriter
if /i "%1" NEQ "0" ( 
                if /i "%1" NEQ "1" ( 
                    echo An error occured by process %2 which finished with result code %1 in %3 seconds. > %4 
                ) 
)