@echo off
rem Production bootstrap launcher - Windows entry (plan section 5).
rem Double-click safe. cmd cannot be blocked by ExecutionPolicy, so it just
rem relaunches PowerShell (which CAN download/verify/install Python) with
rem the policy bypassed for this one process only. All args pass through:
rem   install.bat                  full install
rem   install.bat --check          audit + verify only
rem   install.bat --profile minimal   skip the big torch download
rem
rem Keep this file ASCII-only: cmd echoes byte-for-byte, but a future
rem stripped Windows image with a non-cp1252 console can still mangle
rem comments. There is no reason to take the risk.
setlocal
set "PS_EXE="
where powershell >nul 2>&1
if not errorlevel 1 set "PS_EXE=powershell"
if not defined PS_EXE if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not defined PS_EXE (
  echo [ERROR] Windows PowerShell was not found at the expected location
  echo         %%SystemRoot%%\System32\WindowsPowerShell\v1.0\powershell.exe
  echo         and is not on PATH. It ships with every Windows 7+ install;
  echo         on a stripped image, install PowerShell and re-run.
  echo.
  echo Please contact the developer for assistance.
  echo.
  echo Press Enter to close this window...
  set /p "_dummy="
  exit /b 4
)
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "_EC=%ERRORLEVEL%"

echo.
if %_EC% gtr 1 (
  "%PS_EXE%" -NoProfile -Command "Write-Host '  [FAILED] Installation did not complete (exit %_EC%). Please send install.log to the developer.' -ForegroundColor Red"
) else if %_EC% equ 1 (
  "%PS_EXE%" -NoProfile -Command "Write-Host '  [OK] Installed with warnings. See install.log for details.' -ForegroundColor Yellow"
) else (
  "%PS_EXE%" -NoProfile -Command "Write-Host '  [OK] Installation complete. Ready to run.' -ForegroundColor Green"
)
echo.
echo Press Enter to close this window...
set /p "_dummy="
exit /b %_EC%
