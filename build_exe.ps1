$ErrorActionPreference = "Stop"

$pyinstaller = (Get-Command pyinstaller -ErrorAction Stop).Source

& $pyinstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name "FDS Scheduler" `
  main.py

if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Write-Host "Built: dist\FDS Scheduler\FDS Scheduler.exe"
