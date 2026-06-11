@echo off
REM ============================================================
REM  One-time GitHub setup for Sentinels.
REM  Run this ONCE. It creates the GitHub repo and pushes.
REM  After this, use sync.bat to push future changes.
REM ============================================================
cd /d "%~dp0"
title Sentinels - GitHub setup

echo ============================================================
echo  ANONYMITY NOTICE
echo  Commits are authored as "sentinels" (no real name/email).
echo  But the GitHub ACCOUNT you log in with below becomes the
echo  PUBLIC repo owner. To stay anonymous, log in with an
echo  anonymous GitHub account (not one under your real name).
echo ============================================================
echo.
pause

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
echo === Step 3: put the repo URL on the landing page (config.json) ===
for /f "delims=" %%u in ('gh repo view --json url -q ".url"') do set "REPO_URL=%%u"
if defined REPO_URL (
  python -c "import json;p='config.json';d=json.load(open(p,encoding='utf-8'));d['github_url']='%REPO_URL%';json.dump(d,open(p,'w',encoding='utf-8'),indent=2)"
  echo Landing page GitHub link set to %REPO_URL%
)

echo.
echo Done. Your repo is live on GitHub and linked on the landing page.
echo Future updates: just run  sync.bat
pause
