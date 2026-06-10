@echo off
REM Push local changes to GitHub. Run this whenever you want to sync.
cd /d "%~dp0"
git add -A
git commit -m "Update %DATE% %TIME%"
git push
echo.
echo Synced to GitHub.
pause
