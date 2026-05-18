@echo off
rem Production bootstrap launcher — Windows entry (plan §5).
rem Double-click safe. cmd cannot be blocked by ExecutionPolicy, so it just
rem relaunches PowerShell (which CAN download/verify/install Python) with
rem the policy bypassed for this one process only. All args pass through:
rem   install.bat                  full install
rem   install.bat --check          audit + verify only
rem   install.bat --profile minimal   skip the big torch download
setlocal
where powershell >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Windows PowerShell was not found. It ships with Windows 7+;
  echo         on a stripped image install PowerShell, then re-run.
  exit /b 4
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
exit /b %ERRORLEVEL%
