; Running Pulse programs (the background jobs use the installed backend) lock their files: stop them before files
; are replaced, and start the recorder again afterwards. Errors are ignored (nothing may be running or installed).
!macro NSIS_HOOK_PREINSTALL
  nsExec::Exec 'taskkill /F /T /IM pulsew.exe'
  nsExec::Exec 'taskkill /F /T /IM pulse.exe'
!macroend

!macro NSIS_HOOK_POSTINSTALL
  nsExec::Exec 'schtasks /run /tn pulse-collect'
!macroend
