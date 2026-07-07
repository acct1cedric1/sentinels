@echo off
REM Auto-sync (agents): quietly commit & push local changes. No-op when clean.
REM Called by the Claude Code user-level Stop hook and the Codex AGENTS.md protocol.
cd /d "%~dp0"
git add -A
git diff --cached --quiet || git commit -q -m "auto-sync %DATE% %TIME%"
for /f %%n in ('git rev-list --count origin/main..main 2^>nul') do if not "%%n"=="0" git push -q origin main
exit /b 0
