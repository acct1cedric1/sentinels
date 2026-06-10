@echo off
REM ============================================================
REM  One-time GitHub setup for Sentinels.
REM  Run this ONCE. It creates the GitHub repo and pushes.
REM  After this, use sync.bat to push future changes.
REM ============================================================
cd /d "%~dp0"
title Sentinels - GitHub setup

echo Checking for GitHub CLI...
where gh >nul 2>nul
if errorlevel 1 (
  echo Installing GitHub CLI via winget...
  winget install --id GitHub.cli -e --silent --accept-source-agreements --accept-package-agreements
  echo.
  echo If that failed, install it from https://cli.github.com/ then re-run this.
  echo You may need to open a NEW terminal so 'gh' is on PATH.
  pause
)

echo.
echo === Step 1: authenticate with GitHub (browser opens once) ===
gh auth status >nul 2>nul || gh auth login

echo.
echo === Step 2: create the repo and push ===
gh repo create sentinels --public --source=. --remote=origin --push ^
  --description "Real-time Solana on-chain smart-money intelligence terminal"

echo.
echo Done. Your repo is live on GitHub.
echo Future updates: just run  sync.bat
pause
