@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title AliançaDX Acesso Remoto - Instalação
pushd "%~dp0" || exit /b 1
if not exist "installer_python.py" if exist "..\installer_python.py" cd /d ".."
if not exist "installer_python.py" (
  echo [ERRO] Extraia o pacote completo antes de iniciar a instalacao.
  popd
  exit /b 1
)
set "MINI_ROLE=%~1"
set "MINI_UPDATE="
if /I "%~2"=="--auto-update" set "MINI_UPDATE=--auto-update"
if /I "%MINI_ROLE%"=="controller" goto :valid_role
if /I "%MINI_ROLE%"=="host" goto :valid_role
exit /b 2
:valid_role
set "MINI_PY="
echo ALIANÇADX ACESSO REMOTO 6.2.0 - APLICATIVOS PYTHON
echo Instalacao para o usuario atual. As protecoes do Windows permanecem ativas.
call :find_python
if defined MINI_PY goto :prepare
echo Instalando o Python oficial incluido neste pacote...
start "" /wait "python-3.12.10-amd64.exe" /passive InstallAllUsers=0 Include_launcher=0 InstallLauncherAllUsers=0 Include_test=0 Include_doc=0 Include_dev=0 Include_tcltk=1 Include_pip=1 PrependPath=0 AssociateFiles=0 Shortcuts=0
set "MINI_PYTHON_RESULT=%ERRORLEVEL%"
if not "%MINI_PYTHON_RESULT%"=="0" if not "%MINI_PYTHON_RESULT%"=="3010" goto :python_error
call :find_python
if not defined MINI_PY goto :missing_python
:prepare
%MINI_PY% "installer_python.py" --role %MINI_ROLE% --from-nsis %MINI_UPDATE%
set "MINI_RESULT=%ERRORLEVEL%"
popd
exit /b %MINI_RESULT%
:find_python
set "MINI_PY="
for %%P in (
  "%LocalAppData%\Programs\Python\Python312\python.exe"
  "%LocalAppData%\Programs\Python\Python311\python.exe"
  "%ProgramFiles%\Python312\python.exe"
  "%ProgramFiles%\Python311\python.exe"
) do (
  if exist "%%~P" (
    "%%~P" -c "import sys,struct,tkinter,venv; assert sys.prefix==sys.base_prefix and sys.platform=='win32' and sys.version_info[:2] in ((3,11),(3,12)) and struct.calcsize('P')==8" >nul 2>nul
    if not errorlevel 1 (
      set MINI_PY="%%~P"
      goto :eof
    )
  )
)
where py.exe >nul 2>nul
if not errorlevel 1 (
  for %%V in (3.12 3.11) do (
    py.exe -%%V -c "import sys,struct,tkinter,venv; assert sys.prefix==sys.base_prefix and sys.platform=='win32' and sys.version_info[:2] in ((3,11),(3,12)) and struct.calcsize('P')==8" >nul 2>nul
    if not errorlevel 1 (
      set "MINI_PY=py.exe -%%V"
      goto :eof
    )
  )
)
where python.exe >nul 2>nul
if not errorlevel 1 (
  python.exe -c "import sys,struct,tkinter,venv; assert sys.prefix==sys.base_prefix and sys.platform=='win32' and sys.version_info[:2] in ((3,11),(3,12)) and struct.calcsize('P')==8" >nul 2>nul
  if not errorlevel 1 set "MINI_PY=python.exe"
)
goto :eof
:python_error
echo [ERRO] O instalador oficial do Python retornou %MINI_PYTHON_RESULT%.
echo Finalize a instalacao do Python ou reinicie o Windows, se solicitado.
goto :failed
:missing_python
echo [ERRO] Python 3.11/3.12 de 64 bits com Tcl/Tk nao foi encontrado.
echo Instale o Python 3.12 de 64 bits e execute este instalador novamente.
:failed
pause
popd
exit /b 1
