<div align="center">

# ◎ SENTINELS

### Follow the Solana wallets that actually win.

**Real-time on-chain money-flow intelligence — across the entire actively-traded Solana universe.**

![status](https://img.shields.io/badge/status-live-2bff88?style=flat-square)
![python](https://img.shields.io/badge/python-3.8%2B-2bff88?style=flat-square)
![license](https://img.shields.io/badge/license-MIT-2bff88?style=flat-square)
![deps](https://img.shields.io/badge/build-zero%20build%20step-2bff88?style=flat-square)
![data](https://img.shields.io/badge/data-100%25%20on--chain-2bff88?style=flat-square)

</div>

---

Sentinels discovers the most active wallets on Solana straight from on-chain swaps, scores every
one of them by **realized win rate**, and shows you what they're accumulating — before the crowd
catches on. No bought labels, no black box: every signal is computed live from the chain.

> **Educational analytics, not financial advice.** "Smart money" here is a transparent on-chain
> heuristic (high-volume, high-win-rate active traders), not a vetted track record — it can include
> snipers and bots. Crypto is high-risk; never trade more than you can afford to lose.

## ✨ Features

- **Token flow board** — chain-wide net smart-money flow per token (accumulating vs distributing, buy/sell USD split).
- **Win-rate leaderboard** — every tracked wallet scored by realized-PnL win rate from its own swap history, auto-sorted.
- **Token-gated alpha** — win rates are public; elite-wallet identities unlock by holding the project token (verified on-chain, server-side).
- **Pump.fun Launch Radar** — every new launch incl. pre-bond tokens seconds old, with a smart-money filter. Auto-releases at a market-cap milestone.
- **Universal token search** — paste any contract address to resolve it across **every** Solana DEX, even off the board.
- **Community chat** — wallet-gated, keyword + optional AI moderation.
- **Wallet sign-in** — supports every Solana wallet via the Wallet Standard; a free message signature, never a transaction.

## Data sources

| Source | Role | Key? |
|---|---|---|
| **Helius** | Parses Solana **swaps** into per-wallet buy/sell; DAS holdings & balances. | Free dev key |
| **GeckoTerminal** | Token/pool discovery across **every** Solana DEX, prices, market caps, OHLCV, token lookup. | **No key** |
| **pump.fun** | Newest launches incl. pre-bond tokens, for the Launch Radar. | **No key** |
| **AI moderation** *(optional)* | Configurable LLM endpoint for community-chat moderation (bring your own). | Optional key |

## How "smart money" is defined

No secret label list — the cohort is **auto-discovered** every refresh from real swaps. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full data flow. In short:

1. Discover the actively-traded universe chain-wide (GeckoTerminal trending + top-volume pools).
2. For each token, Helius parses recent **swaps**: `feePayer` = trader, the SOL/USDC/USDT leg = USD size, direction = buy vs sell.
3. Aggregate two ways:
   - **Tokens tab:** per token — active smart wallets, total **buys** vs **sells**, **net flow** → *Accumulating / Distributing / Mixed*.
   - **Smart Money tab:** each wallet rolled up across every token it touched — bought, sold, net, volume, # tokens, # trades, **win rate**, top positions.

### Win rate

The Top Traders table shows a **win rate** per wallet and is **sorted by it** by default. Win rate =
realized-PnL win/loss ratio computed from the wallet's *own* recent swap history (Helius), using an
average-cost basis. Only wallets with at least `SM_MIN_CLOSED` (3) closed round-trips are ranked by
win rate; the rest fall back to volume ranking. Scored for the top `SM_WINRATE_N` (22) wallets per
refresh and cached 30 min each.

### Premium gating + wallet login

The Top Traders section is **token-gated, server-side**. Free users see every win rate, but the
**identities of high-win-rate wallets are withheld from the API** (not just CSS-blurred) — addresses
and traded coins never leave the server for non-holders.

- **Login:** click *Connect Wallet*. Uses the **Solana Wallet Standard**, so *every* installed wallet
  (Phantom, Solflare, Backpack, OKX, Coinbase, Glow, …) is auto-detected — nothing hardcoded. You sign
  a one-time message (free, no transaction); the server verifies the **ed25519 signature** to prove
  ownership, then checks your on-chain gate-token balance and issues a signed session cookie.
- **Unlock rule:** hold ≥ `min_usd` (USD-valued live) of `token_mint`, **or** be listed in
  `admin_wallets`.
- Configure in `config.json`:
  ```json
  "gating": {
    "enabled": true,
    "lock_winrate": 50,        // wallets with win rate >= this are gated for free users
    "token_mint": "",          // your SPL token mint — blank = PREVIEW mode
    "min_usd": 5,              // hold >= $5 worth to unlock
    "admin_wallets": []        // pubkeys that are always premium (e.g. you)
  }
  ```
- **Before your token launches:** leave `token_mint` blank → the app runs in **preview mode**: free
  users see the locked experience, and only wallets in `admin_wallets` can preview the unlocked view.
  Put your own wallet address in `admin_wallets` to always have full access.
- **At launch:** drop your mint into `token_mint` and holders unlock automatically. Set `enabled:false`
  to turn gating off entirely (everyone sees everything).
- Crypto: uses the `cryptography` package for fast ed25519 verify when present, with a self-contained
  pure-python fallback. Auth endpoints: `/api/auth/nonce`, `/api/auth/verify`, `/api/auth/status`,
  `/api/auth/logout`.

### Pump.fun Launch Radar (milestone-gated)

Third dashboard tab: a live feed of **every new pump.fun launch — including un-bonded tokens seconds
old** (the newest rows are 0–10s old, ages tick live, feed refreshes every 6s). The **"Smart money
only"** checkbox cross-references each launch's bonding-curve buys (via Helius) against the tracked
smart-money cohort and keeps only launches an elite wallet already bought.

**Milestone auto-release:** the radar is locked for everyone until the project token's market cap
reaches `gating.pumpfun_mcap_target` (default **$100,000**, checked live via GeckoTerminal). The
server enforces this (`/api/pumpfun` withholds rows while locked) and unlocks automatically the
moment the milestone is hit — no redeploy. `admin_wallets` can preview it before launch.

### Landing page

`http://localhost:8050/` now serves a landing page (hero, live stats, features, the live $100K
milestone progress bar, 5 FAQs, and a market-cap roadmap). The dashboard lives at
`http://localhost:8050/app`.

Click any row for a detail drawer (price sparkline + per-trader breakdown for tokens; holdings +
explorer links for wallets). Wallet nicknames (e.g. *"Silent Whale"*) are **generated** from the
address — they are not real identities.

---

## Setup

1. **Get a free Helius key:** <https://helius.dev> → sign up → **Dashboard → API Keys** → copy the key.
2. **Give the app your key** — any one of:
   - copy `config.example.json` → `config.json` and paste your key, **or**
   - set an env var: `set HELIUS_API_KEY=your_key` (Windows) before launching, **or**
   - put the raw key on one line in `api_key.txt`.
3. **Run it:** double-click `start.bat` → open <http://localhost:8050>.
   (Port is the `set PORT=8050` line at the top of `start.bat`. `python server.py` directly
   defaults to 8000 unless you `set PORT` first.)

No external Python packages are required — it uses the standard library, and `requests` if present.
GeckoTerminal needs no key or signup.

---

## Controls

| Control | What it does |
|---|---|
| **Timeframe** (1h / 4h / 8h / 24h) | How many recent swaps to scan per pool (rough window). |
| **Flow** | Filter tokens to *Accumulating* or *Distributing* only. |
| **Memecoins only** | Toggle the majors/stables filter off to see all trending tokens. |
| **Search** | Filter by token symbol/name/mint, or wallet alias/address. |
| Column headers | Click to sort; click again to reverse. |
| **↻** | Force refresh (bypasses the cache). |

Data auto-refreshes every 5 minutes; results are cached server-side (`SM_TTL`, default 300s).

---

## Tuning (env vars)

| Var | Default | Meaning |
|---|---|---|
| `HELIUS_API_KEY` | — | Your key (or use `config.json` / `api_key.txt`). |
| `HELIUS_RPS` | `8` | Max requests/sec to Helius. |
| `GT_RPS` | `2` | Max requests/sec to GeckoTerminal (free limit ≈ 30/min). |
| `SM_MAX_TOKENS` | `100` | How many of the most-active tokens chain-wide to scan per refresh (= Helius calls/refresh). |
| `SM_MAX_WALLETS` | `120` | Size of the Smart Money wallet leaderboard. |
| `SM_MIN_TRADE_USD` | `10` | Ignore swaps smaller than this (dust filter). |
| `SM_SOL_PRICE` | `150` | Fallback SOL price if it can't be fetched. |
| `SM_TTL` | `300` | Server cache lifetime, seconds. |
| `SM_WORKERS` | `4` | Parallel pool scans. |
| `PORT` | `8000` | HTTP port (start.bat sets 8050). |

---

## API (raw JSON)

- `GET /api/smartmoney?tf=24h&memecoins=1` — the full board (tokens + wallets).
- `GET /api/token?address=<mint>&tf=24h` — token sparkline (pool OHLCV).
- `GET /api/wallet?address=<wallet>` — wallet holdings (Helius DAS).
- `GET /api/health` — `{ ok, key, http }`.

---

## Files

```
solana-smart-money/
├─ server.py            # HTTP server + discovery/aggregation + win-rate + gating
├─ helius.py            # Helius client: enhanced swaps + DAS holdings + balances
├─ market.py            # GeckoTerminal client: trending pools + prices (no key)
├─ auth.py              # wallet sign-in (ed25519) + sessions + token-gating logic
├─ pumpfun.py           # pump.fun launch feed + smart-buyer detection (no key)
├─ start.bat            # Windows launcher (PORT set at top)
├─ config.example.json  # copy to config.json and add your Helius key
└─ static/
   ├─ landing.html      # landing page (served at /): FAQs, roadmap, milestone bar
   ├─ index.html        # the dashboard (served at /app)
   ├─ style.css
   ├─ app.js
   └─ wallet.js         # Wallet Standard login (all Solana wallets)
```

## Notes & limits

- "Smart money" here is a *heuristic* (high-volume recent traders of hot tokens), not a vetted
  track record. It includes snipers and MEV bots — treat the signal accordingly.
- USD sizing values the SOL/USDC/USDT leg of each swap; exotic multi-hop routes may be approximate.
- The per-pool scan reads the most recent ~100 swaps, so on very hot tokens the window is "recent
  activity," not a strict 24h. Memecoins are extremely high-risk — analytics toy, not advice.
