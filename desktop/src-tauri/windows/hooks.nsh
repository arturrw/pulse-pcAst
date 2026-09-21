; Running Pulse programs (the background jobs use the installed backend) lock their files: stop them before files
; are replaced, and start the recorder again afterwards. Errors are ignored (nothing may be running or installed).
!macro NSIS_HOOK_PREINSTALL
  nsExec::Exec 'taskkill /F /T /IM pulsew.exe'
  nsExec::Exec 'taskkill /F /T /IM pulse.exe'
!macroend

; pulse://<page> opens the app on that page: a clicked notification uses it. "Pulse.Desktop" makes notifications say
; "Pulse" instead of "Windows PowerShell". Both live under the current user only.
!macro NSIS_HOOK_POSTINSTALL
  WriteRegStr HKCU "Software\Classes\pulse" "" "URL:Pulse"
  WriteRegStr HKCU "Software\Classes\pulse" "URL Protocol" ""
  WriteRegStr HKCU "Software\Classes\pulse\shell\open\command" "" '"$INSTDIR\pulse-desktop.exe" "%1"'
  WriteRegStr HKCU "Software\Classes\AppUserModelId\Pulse.Desktop" "DisplayName" "Pulse"
  nsExec::Exec 'schtasks /run /tn pulse-collect'
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  DeleteRegKey HKCU "Software\Classes\pulse"
  DeleteRegKey HKCU "Software\Classes\AppUserModelId\Pulse.Desktop"
!macroend
