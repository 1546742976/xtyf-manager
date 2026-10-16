; Upgrade the prior per-user electron-builder package without deleting user data.
!macro NSIS_HOOK_PREINSTALL
  ReadRegStr $R0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\67fef204-b908-5e28-9ea5-51e24f236a62" "UninstallString"
  StrCmp $R0 "" xtyf_old_done
  ExecWait '$R0 /S /KEEP_APP_DATA' $R1
  IntCmp $R1 0 xtyf_old_done
  MessageBox MB_ICONSTOP "旧版客户端未能卸载，请关闭旧版后重试。"
  Abort
  xtyf_old_done:
!macroend
