@echo off
cd /d "%~dp0.."
if not exist ".venv\Scripts\pythonw.exe" (
  echo OpenShelf's root .venv is missing. Run the pipeline setup in README.md first.
  pause
  exit /b 1
)
start "OpenShelf Job Monitor" ".venv\Scripts\pythonw.exe" "pipeline\scripts\job-monitor.pyw"
