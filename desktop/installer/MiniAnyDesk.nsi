Unicode true
Target amd64-unicode
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "WinVer.nsh"
!include "x64.nsh"

!ifndef PROJECT_ROOT
  !error "Defina PROJECT_ROOT."
!endif
!ifndef ASSET_ROOT
  !error "Defina ASSET_ROOT."
!endif
!ifndef ROLE
  !error "Defina ROLE=controller ou ROLE=host."
!endif
!if "${ROLE}" == "controller"
  !define APP_LABEL "Controlador"
  !define APP_DIR "Controller"
  !define ENTRY "controller.py"
!else if "${ROLE}" == "host"
  !define APP_LABEL "Host"
  !define APP_DIR "Host"
  !define ENTRY "host.py"
!else
  !error "ROLE invalido."
!endif

!ifndef OUTPUT_ROOT
  !define OUTPUT_ROOT "${PROJECT_ROOT}/dist"
!endif

; These hooks are used only by a publisher-controlled signing build. A normal
; Linux build remains unsigned; metadata never substitutes for Authenticode.
; NSIS supplies the generated executable's path as %1 to each command.
!ifdef UNINSTALL_SIGN_COMMAND
  !uninstfinalize `${UNINSTALL_SIGN_COMMAND}` = 0
!endif
!ifdef INSTALLER_SIGN_COMMAND
  !finalize `${INSTALLER_SIGN_COMMAND}` = 0
!endif

Name "AliançaDX Acesso Remoto ${APP_LABEL} 6.2.1"
OutFile "${OUTPUT_ROOT}/AliancaDX_${APP_LABEL}_Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\MiniAnyDesk\${APP_DIR}"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show
Var BackupFolder
Var InstallMutex
Var SourceRollbackPending
VIProductVersion "6.2.1.0"
VIAddVersionKey /LANG=1046 "ProductName" "AliançaDX Acesso Remoto ${APP_LABEL}"
VIAddVersionKey /LANG=1046 "CompanyName" "AliançaDX — Bruno Michel Pera"
VIAddVersionKey /LANG=1046 "ProductVersion" "6.2.1"
VIAddVersionKey /LANG=1046 "FileVersion" "6.2.1.0"
VIAddVersionKey /LANG=1046 "FileDescription" "Instalador do AliançaDX Acesso Remoto ${APP_LABEL} em Python"
VIAddVersionKey /LANG=1046 "InternalName" "AliancaDX-${APP_DIR}-Setup"
VIAddVersionKey /LANG=1046 "OriginalFilename" "AliancaDX_${APP_LABEL}_Setup.exe"
VIAddVersionKey /LANG=1046 "LegalCopyright" "AliançaDX — Bruno Michel Pera; licencas de terceiros incluidas"

!define MUI_ICON "${PROJECT_ROOT}/assets/brand/aliancadx.ico"
!define MUI_UNICON "${PROJECT_ROOT}/assets/brand/aliancadx.ico"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "${PROJECT_ROOT}/assets/brand/aliancadx-header.bmp"
!define MUI_WELCOMEFINISHPAGE_BITMAP "${PROJECT_ROOT}/assets/brand/aliancadx-wizard.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "${PROJECT_ROOT}/assets/brand/aliancadx-wizard.bmp"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Instalar AliançaDX$\r$\n${APP_LABEL}"
!define MUI_WELCOMEPAGE_TEXT "Aplicativo nativo Python para Windows 10/11 x64.$\r$\n$\r$\nPython, bibliotecas e componentes de acesso pela internet estao incluidos.$\r$\n$\r$\nA rede integrada funciona sem login, sem adaptador VPN e sem servico adicional no Windows. Abra os programas para conectar."
!define MUI_FINISHPAGE_TEXT "AliançaDX Acesso Remoto ${APP_LABEL} foi instalado para este usuario.$\r$\n$\r$\nA instalacao terminou. Abra o aplicativo manualmente pelo atalho quando desejar utiliza-lo.$\r$\n$\r$\nNo acesso remoto, o Host solicita senha e autorizacao local para cada sessao."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${PROJECT_ROOT}/installer/NOTICE.txt"
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "PortugueseBR"

!macro BackupFile NAME
  IfFileExists "$INSTDIR\${NAME}" 0 +2
  CopyFiles /SILENT "$INSTDIR\${NAME}" "$BackupFolder"
!macroend

!macro RestoreFile NAME
  IfFileExists "$BackupFolder\${NAME}" 0 +3
  CopyFiles /SILENT "$BackupFolder\${NAME}" "$INSTDIR"
  Goto +3
  IfFileExists "$INSTDIR\${NAME}" 0 +2
  Delete "$INSTDIR\${NAME}"
!macroend

!macro BackupBrandFile NAME
  IfFileExists "$INSTDIR\assets\brand\${NAME}" 0 +2
  CopyFiles /SILENT "$INSTDIR\assets\brand\${NAME}" "$BackupFolder\assets\brand"
!macroend

!macro RestoreBrandFile NAME
  ${If} ${FileExists} "$BackupFolder\assets\brand\${NAME}"
    CreateDirectory "$INSTDIR\assets\brand"
    CopyFiles /SILENT "$BackupFolder\assets\brand\${NAME}" "$INSTDIR\assets\brand"
  ${Else}
    Delete "$INSTDIR\assets\brand\${NAME}"
  ${EndIf}
!macroend

!macro RestorePayload
  ClearErrors
  !insertmacro RestoreFile "controller.py"
  !insertmacro RestoreFile "host.py"
  !insertmacro RestoreFile "net.py"
  !insertmacro RestoreFile "adaptive_stream.py"
  !insertmacro RestoreFile "transfer.py"
  !insertmacro RestoreFile "desktop_geometry.py"
  !insertmacro RestoreFile "desktop_ui.py"
  !insertmacro RestoreFile "computer_book.py"
  !insertmacro RestoreFile "presence.py"
  !insertmacro RestoreFile "network_addresses.py"
  !insertmacro RestoreFile "mesh.py"
  !insertmacro RestoreFile "mesh_ui.py"
  !insertmacro RestoreFile "installer_python.py"
  !insertmacro RestoreFile "requirements.txt"
  !insertmacro RestoreFile "COMECE_AQUI.html"
  !insertmacro RestoreFile "GUIA_RAPIDO.txt"
  !insertmacro RestoreFile "CONFIANCA_WINDOWS.txt"
  !insertmacro RestoreFile "README.txt"
  !insertmacro RestoreFile "NOTICE.txt"
  !insertmacro RestoreFile "Abrir_AliancaDX_Host.cmd"
  !insertmacro RestoreFile "Abrir_AliancaDX_Controlador.cmd"
  !insertmacro RestoreBrandFile "aliancadx-logo.png"
  !insertmacro RestoreBrandFile "aliancadx-logo-original.jpg"
  !insertmacro RestoreBrandFile "aliancadx-mark-256.png"
  !insertmacro RestoreBrandFile "aliancadx-app-256.png"
  !insertmacro RestoreBrandFile "aliancadx.ico"
  IfFileExists "$INSTDIR\easytier_bundle\*.*" 0 +2
  RMDir /r "$INSTDIR\easytier_bundle"
  IfFileExists "$BackupFolder\easytier_bundle\*.*" 0 +3
  CreateDirectory "$INSTDIR\easytier_bundle"
  CopyFiles /SILENT "$BackupFolder\easytier_bundle\*.*" "$INSTDIR\easytier_bundle"
  ${If} ${Errors}
    DetailPrint "A restauracao de alguns fontes falhou. Feche o aplicativo e verifique as permissoes da pasta antes de tentar novamente."
  ${Else}
    DetailPrint "Fontes anteriores restaurados; novos modulos incompletos removidos."
  ${EndIf}
  RMDir "$INSTDIR\assets\brand"
  RMDir "$INSTDIR\assets"
!macroend

Function .onInit
  SetShellVarContext current
  System::Call 'kernel32::CreateMutexW(p 0, i 0, w "Local\MiniAnyDeskInstaller${APP_DIR}") p .r0 ?e'
  Pop $1
  StrCpy $InstallMutex $0
  ${If} $1 = 183
    MessageBox MB_OK|MB_ICONSTOP "Outra instalacao de AliançaDX Acesso Remoto ${APP_LABEL} esta em andamento."
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_OK|MB_ICONSTOP "Este aplicativo requer Windows 10 ou Windows 11."
    Abort
  ${EndIf}
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "Este pacote requer Windows x64."
    Abort
  ${EndIf}
FunctionEnd

Function .onInstFailed
  ${If} $SourceRollbackPending = 1
    !insertmacro RestorePayload
    StrCpy $SourceRollbackPending 0
  ${EndIf}
FunctionEnd

Section "AliançaDX Acesso Remoto ${APP_LABEL}" SEC_MAIN
  SetShellVarContext current
  InitPluginsDir
  StrCpy $BackupFolder "$PLUGINSDIR\existing"
  CreateDirectory "$BackupFolder"
  CreateDirectory "$BackupFolder\assets\brand"
  ClearErrors
  !insertmacro BackupFile "controller.py"
  !insertmacro BackupFile "host.py"
  !insertmacro BackupFile "net.py"
  !insertmacro BackupFile "adaptive_stream.py"
  !insertmacro BackupFile "transfer.py"
  !insertmacro BackupFile "desktop_geometry.py"
  !insertmacro BackupFile "desktop_ui.py"
  !insertmacro BackupFile "computer_book.py"
  !insertmacro BackupFile "presence.py"
  !insertmacro BackupFile "network_addresses.py"
  !insertmacro BackupFile "mesh.py"
  !insertmacro BackupFile "mesh_ui.py"
  !insertmacro BackupFile "installer_python.py"
  !insertmacro BackupFile "requirements.txt"
  !insertmacro BackupFile "COMECE_AQUI.html"
  !insertmacro BackupFile "GUIA_RAPIDO.txt"
  !insertmacro BackupFile "CONFIANCA_WINDOWS.txt"
  !insertmacro BackupFile "README.txt"
  !insertmacro BackupFile "NOTICE.txt"
  !insertmacro BackupFile "Abrir_AliancaDX_Host.cmd"
  !insertmacro BackupFile "Abrir_AliancaDX_Controlador.cmd"
  !insertmacro BackupBrandFile "aliancadx-logo.png"
  !insertmacro BackupBrandFile "aliancadx-logo-original.jpg"
  !insertmacro BackupBrandFile "aliancadx-mark-256.png"
  !insertmacro BackupBrandFile "aliancadx-app-256.png"
  !insertmacro BackupBrandFile "aliancadx.ico"
  IfFileExists "$INSTDIR\easytier_bundle\*.*" 0 +3
  CreateDirectory "$BackupFolder\easytier_bundle"
  CopyFiles /SILENT "$INSTDIR\easytier_bundle\*.*" "$BackupFolder\easytier_bundle"
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONSTOP "Nao foi possivel preservar a instalacao anterior. Verifique o espaco em disco e feche o aplicativo antes de atualizar."
    SetErrorLevel 1
    Abort
  ${EndIf}
  ClearErrors
  StrCpy $SourceRollbackPending 1
  SetOutPath "$INSTDIR"
  File "${PROJECT_ROOT}/controller.py"
  File "${PROJECT_ROOT}/host.py"
  File "${PROJECT_ROOT}/net.py"
  File "${PROJECT_ROOT}/adaptive_stream.py"
  File "${PROJECT_ROOT}/transfer.py"
  File "${PROJECT_ROOT}/desktop_geometry.py"
  File "${PROJECT_ROOT}/desktop_ui.py"
  File "${PROJECT_ROOT}/computer_book.py"
  File "${PROJECT_ROOT}/presence.py"
  File "${PROJECT_ROOT}/network_addresses.py"
  File "${PROJECT_ROOT}/mesh.py"
  File "${PROJECT_ROOT}/mesh_ui.py"
  File "${PROJECT_ROOT}/installer_python.py"
  File "${PROJECT_ROOT}/requirements.txt"
  File "${PROJECT_ROOT}/COMECE_AQUI.html"
  File "${PROJECT_ROOT}/GUIA_RAPIDO.txt"
  File "${PROJECT_ROOT}/CONFIANCA_WINDOWS.txt"
  File /nonfatal "${PROJECT_ROOT}/README.txt"
  File "${PROJECT_ROOT}/installer/NOTICE.txt"
  File "${PROJECT_ROOT}/installer/bootstrap.cmd"
  File "${PROJECT_ROOT}/Abrir_AliancaDX_Host.cmd"
  File "${PROJECT_ROOT}/Abrir_AliancaDX_Controlador.cmd"
  SetOutPath "$INSTDIR\assets\brand"
  File "${PROJECT_ROOT}/assets/brand/aliancadx-logo.png"
  File "${PROJECT_ROOT}/assets/brand/aliancadx-logo-original.jpg"
  File "${PROJECT_ROOT}/assets/brand/aliancadx-mark-256.png"
  File "${PROJECT_ROOT}/assets/brand/aliancadx-app-256.png"
  File "${PROJECT_ROOT}/assets/brand/aliancadx.ico"
  SetOutPath "$INSTDIR"
  File "${ASSET_ROOT}/python-3.12.10-amd64.exe"
  SetOutPath "$INSTDIR\wheelhouse"
  File "${ASSET_ROOT}/wheelhouse/*.whl"
  SetOutPath "$INSTDIR\easytier_bundle"
  File "${ASSET_ROOT}/easytier_bundle/*"
  SetOutPath "$INSTDIR"
  ${If} ${Errors}
    !insertmacro RestorePayload
    StrCpy $SourceRollbackPending 0
    MessageBox MB_OK|MB_ICONSTOP "Nao foi possivel extrair todos os arquivos. Verifique o espaco em disco e feche o aplicativo antes de atualizar. Consulte os detalhes da restauracao nesta janela."
    SetErrorLevel 1
    Abort
  ${EndIf}
  DetailPrint "Preparando Python e abrindo o instalador Python de ${APP_LABEL}..."
  ExecWait '"$SYSDIR\cmd.exe" /D /C ""$INSTDIR\bootstrap.cmd" ${ROLE}"' $0
  ${If} $0 != 0
    !insertmacro RestorePayload
    StrCpy $SourceRollbackPending 0
    DetailPrint "A preparacao nao foi concluida (codigo $0)."
    MessageBox MB_OK|MB_ICONSTOP "A instalacao foi cancelada ou uma etapa falhou. Consulte instalacao.log em $INSTDIR.$\r$\n$\r$\nFeche o aplicativo e execute o instalador novamente."
    SetErrorLevel 1
    Abort
  ${EndIf}
  StrCpy $SourceRollbackPending 0
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\AliançaDX Acesso Remoto"
  CreateShortCut "$SMPROGRAMS\AliançaDX Acesso Remoto\AliançaDX Acesso Remoto ${APP_LABEL}.lnk" "$INSTDIR\.venv\Scripts\pythonw.exe" '"$INSTDIR\${ENTRY}"' "$INSTDIR\assets\brand\aliancadx.ico" 0 SW_SHOWNORMAL "" "Abrir AliançaDX Acesso Remoto ${APP_LABEL}"
  CreateShortCut "$DESKTOP\AliançaDX Acesso Remoto ${APP_LABEL}.lnk" "$INSTDIR\.venv\Scripts\pythonw.exe" '"$INSTDIR\${ENTRY}"' "$INSTDIR\assets\brand\aliancadx.ico" 0 SW_SHOWNORMAL "" "Abrir AliançaDX Acesso Remoto ${APP_LABEL}"
  CreateShortCut "$SMPROGRAMS\AliançaDX Acesso Remoto\Desinstalar ${APP_LABEL}.lnk" "$INSTDIR\Uninstall.exe" "" "$INSTDIR\assets\brand\aliancadx.ico"
  ; Remove only this role's known shortcuts from previous releases.
  Delete "$DESKTOP\MiniAnyDesk ${APP_LABEL}.lnk"
  Delete "$SMPROGRAMS\MiniAnyDesk ${APP_LABEL}.lnk"
  Delete "$SMPROGRAMS\MiniAnyDesk\MiniAnyDesk ${APP_LABEL}.lnk"
  Delete "$SMPROGRAMS\MiniAnyDesk\Desinstalar ${APP_LABEL}.lnk"
  RMDir "$SMPROGRAMS\MiniAnyDesk"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MiniAnyDeskNative${APP_DIR}" "DisplayName" "AliançaDX Acesso Remoto ${APP_LABEL}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MiniAnyDeskNative${APP_DIR}" "DisplayVersion" "6.2.1"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MiniAnyDeskNative${APP_DIR}" "Publisher" "AliançaDX — Bruno Michel Pera"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MiniAnyDeskNative${APP_DIR}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MiniAnyDeskNative${APP_DIR}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MiniAnyDeskNative${APP_DIR}" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MiniAnyDeskNative${APP_DIR}" "NoRepair" 1
  DetailPrint "AliançaDX Acesso Remoto ${APP_LABEL} instalado para este usuario."
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  MessageBox MB_OKCANCEL|MB_ICONINFORMATION "Feche AliançaDX Acesso Remoto ${APP_LABEL} antes de continuar.$\r$\n$\r$\nConfiguracoes, certificados, computadores salvos, arquivos recebidos e o Python principal serao preservados." IDOK continue
  Abort
  continue:
  ClearErrors
  RMDir /r "$INSTDIR\.venv"
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONSTOP "Nao foi possivel remover o ambiente privado. Feche o aplicativo e execute o desinstalador novamente."
    SetErrorLevel 1
    Abort
  ${EndIf}
  Delete "$DESKTOP\AliançaDX Acesso Remoto ${APP_LABEL}.lnk"
  Delete "$SMPROGRAMS\AliançaDX Acesso Remoto ${APP_LABEL}.lnk"
  Delete "$SMPROGRAMS\AliançaDX Acesso Remoto\AliançaDX Acesso Remoto ${APP_LABEL}.lnk"
  Delete "$SMPROGRAMS\AliançaDX Acesso Remoto\Desinstalar ${APP_LABEL}.lnk"
  RMDir "$SMPROGRAMS\AliançaDX Acesso Remoto"
  Delete "$DESKTOP\MiniAnyDesk ${APP_LABEL}.lnk"
  Delete "$SMPROGRAMS\MiniAnyDesk ${APP_LABEL}.lnk"
  Delete "$SMPROGRAMS\MiniAnyDesk\MiniAnyDesk ${APP_LABEL}.lnk"
  Delete "$SMPROGRAMS\MiniAnyDesk\Desinstalar ${APP_LABEL}.lnk"
  RMDir "$SMPROGRAMS\MiniAnyDesk"
  Delete "$INSTDIR\controller.py"
  Delete "$INSTDIR\host.py"
  Delete "$INSTDIR\net.py"
  Delete "$INSTDIR\adaptive_stream.py"
  Delete "$INSTDIR\transfer.py"
  Delete "$INSTDIR\desktop_geometry.py"
  Delete "$INSTDIR\desktop_ui.py"
  Delete "$INSTDIR\computer_book.py"
  Delete "$INSTDIR\presence.py"
  Delete "$INSTDIR\network_addresses.py"
  Delete "$INSTDIR\mesh.py"
  Delete "$INSTDIR\mesh_ui.py"
  Delete "$INSTDIR\vpn.py"
  Delete "$INSTDIR\vpn_ui.py"
  Delete "$INSTDIR\auto_connect.py"
  Delete "$INSTDIR\vpn_setup.py"
  Delete "$INSTDIR\installer_python.py"
  Delete "$INSTDIR\requirements.txt"
  Delete "$INSTDIR\COMECE_AQUI.html"
  Delete "$INSTDIR\GUIA_RAPIDO.txt"
  Delete "$INSTDIR\CONFIANCA_WINDOWS.txt"
  Delete "$INSTDIR\README.txt"
  Delete "$INSTDIR\NOTICE.txt"
  Delete "$INSTDIR\Abrir_AliancaDX_Host.cmd"
  Delete "$INSTDIR\Abrir_AliancaDX_Controlador.cmd"
  Delete "$INSTDIR\assets\brand\aliancadx-logo.png"
  Delete "$INSTDIR\assets\brand\aliancadx-logo-original.jpg"
  Delete "$INSTDIR\assets\brand\aliancadx-mark-256.png"
  Delete "$INSTDIR\assets\brand\aliancadx-app-256.png"
  Delete "$INSTDIR\assets\brand\aliancadx.ico"
  RMDir "$INSTDIR\assets\brand"
  RMDir "$INSTDIR\assets"
  Delete "$INSTDIR\bootstrap.cmd"
  Delete "$INSTDIR\python-3.12.10-amd64.exe"
  Delete "$INSTDIR\instalacao.log"
  Delete "$INSTDIR\.minianydesk-app.json"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir /r "$INSTDIR\wheelhouse"
  RMDir /r "$INSTDIR\easytier_bundle"
  RMDir /r "$INSTDIR\vpn_bundle"
  RMDir /r "$INSTDIR\__pycache__"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MiniAnyDeskNative${APP_DIR}"
  DetailPrint "AliançaDX Acesso Remoto ${APP_LABEL} removido. Seus dados foram preservados."
SectionEnd
