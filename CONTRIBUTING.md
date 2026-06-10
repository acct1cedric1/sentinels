# Contributing to Sentinels

Thanks for your interest in improving Sentinels. This document explains how the
project is organized and how to propose changes.

## Project layout

```
sentinels/
├─ server.py      # HTTP server, discovery + aggregation engine, all /api routes
├─ helius.py      # Helius client (parsed swaps, DAS holdings, balances)
├─ market.py      # GeckoTerminal client (trending/top pools, prices, token lookup)
├─ pumpfun.py     # pump.fun launch feed + smart-buyer cross-reference
├─ auth.py        # wallet sign-in (ed25519), sessions, token-gating
├─ chat.py        # community chat + moderation (keyword + optional Claude AI)
└─ static/        # frontend (vanilla HTML/CSS/JS, no build step)
```

There is **no build step** and the only hard dependency is the Python standard
library (`requests` and `cryptography` are used when present, with fallbacks).

## Local setup

1. Copy `config.example.json` → `config.json` and add your Helius API key.
2. `python server.py` (or `start.bat`) → open `http://localhost:8050`.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how data flows through the system.

## Ground rules

- **Never commit secrets.** `config.json`, `api_key.txt`, and `.session_secret`
  are git-ignored. Double-check `git status` before committing.
- Keep the frontend dependency-free (vanilla JS, no bundler).
- Match the existing code style: compact, defensive (network calls degrade
  gracefully into empty results rather than crashing).
- New external calls must be rate-limited and cached.

## Pull requests

1. Fork and create a feature branch (`feat/my-change`).
2. Make focused commits with clear messages.
3. Describe the change and how you tested it.
4. Open the PR against `main`.

## Reporting issues

Use GitHub Issues. For security-sensitive reports (e.g. a key-leak vector),
please open a private security advisory rather than a public issue.
