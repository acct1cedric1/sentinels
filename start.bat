@echo off
title Solana Smart Money Tracker

REM ---- Port (change this number to use a different port) ----
set PORT=8050

REM Optional: set your key here instead of config.json
REM set HELIUS_API_KEY=your_key_here

echo Starting Solana Smart Money tracker...
echo Open  ->  http://localhost:%PORT%
echo.
python "%~dp0server.py"
pause
