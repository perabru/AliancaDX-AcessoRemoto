@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title AliançaDX Acesso Remoto - Controlador
set "APPDIR=%LOCALAPPDATA%\Programs\MiniAnyDesk\Controller"
if not exist "%APPDIR%\.venv\Scripts\pythonw.exe" goto missing
if not exist "%APPDIR%\controller.py" goto missing
start "AliançaDX Acesso Remoto Controlador" /D "%APPDIR%" "%APPDIR%\.venv\Scripts\pythonw.exe" "%APPDIR%\controller.py"
exit /b 0
:missing
echo Instale o AliançaDX Acesso Remoto Controlador antes de abrir.
echo Use dist\AliancaDX_Controlador_Setup.exe no pacote extraido.
pause
exit /b 1
