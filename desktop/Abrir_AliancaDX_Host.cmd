@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title AliançaDX Acesso Remoto - Host
set "APPDIR=%LOCALAPPDATA%\Programs\MiniAnyDesk\Host"
if not exist "%APPDIR%\.venv\Scripts\pythonw.exe" goto missing
if not exist "%APPDIR%\host.py" goto missing
start "AliançaDX Acesso Remoto Host" /D "%APPDIR%" "%APPDIR%\.venv\Scripts\pythonw.exe" "%APPDIR%\host.py"
exit /b 0
:missing
echo Instale o AliançaDX Acesso Remoto Host antes de abrir.
echo Use dist\AliancaDX_Host_Setup.exe no pacote extraido.
pause
exit /b 1
