# Architecture

Sentinels is a single-process Python application: a standard-library HTTP server
that serves a vanilla-JS frontend and a small set of JSON APIs. There is no
database and no build step — state lives in memory with light file persistence,
and all market intelligence is computed live from public on-chain data.

## Data sources

| Source | Role | Auth |
|---|---|---|
| **Helius** | Parses Solana transactions into clean swaps (who traded, which tokens, how much); DAS holdings & balances. | API key |
| **GeckoTerminal** | Token/pool discovery across **every** Solana DEX, prices, market caps, OHLCV, arbitrary token lookup. | none |
| **pump.fun** | Newest launches incl. pre-bond tokens, for the Launch Radar. | none |
| **AI moderation (optional)** | Configurable LLM endpoint for community-chat moderation. | API key (optional) |

## The core loop — "base scan, then derive"

The expensive work happens **once** per refresh cycle and everything else is
derived from it in memory:

```
build_base()                         # ~once / 5 min, the only slow path
  ├─ discover the actively-traded universe
  │    GeckoTerminal trending_pools + top_pools(by 24h volume)
  │    → dedupe to one pool per mint → rank by volume → cap (SM_MAX_TOKENS)
  ├─ for each pool: Helius parses recent swaps → raw records
  │    (wallet, side, usd, timestamp)            # kept raw, with timestamps
  └─ warm the win-rate cache for the most active wallets

build_smart_money(timeframe, memecoins_only)     # instant, no API calls
  ├─ filter each token's raw records by the timeframe window
  ├─ aggregate per-token flow + per-wallet leaderboard
  └─ attach win rates (already cached) → sort
```

Because timeframe and "memecoins only" are pure in-memory filters over the cached
base, switching them is instant after the first warm-up.

## Win rate

A wallet's win rate is computed from **its own** recent swap history (Helius),
reconstructing positions on an average-cost basis and counting the share of
closed trades that were profitable. It is independent of the board timeframe and
cached per wallet (~30 min).

## Token gating (server-side)

Win rates are public; the **identities** of elite wallets (win rate ≥ threshold)
are withheld from the API response for non-holders — the addresses never leave
the server, so the gate cannot be bypassed from the browser. Unlock requires a
wallet to (a) prove ownership via an ed25519 message signature and (b) hold the
configured token balance, verified on-chain.

## Milestone auto-release

Features can be gated behind a project-token market-cap milestone (e.g. the
Pump.fun Launch Radar at $100K). The server checks the live market cap each
request and unlocks the feature for everyone once the threshold is crossed.

## Request map

| Route | Purpose |
|---|---|
| `GET /` , `GET /app` | Landing page / dashboard |
| `GET /api/smartmoney?tf=&memecoins=` | Token flow board + wallet leaderboard |
| `GET /api/lookup?address=` | Resolve any token by mint (off-board search) |
| `GET /api/token` , `/api/wallet` | Drawer detail |
| `GET /api/pumpfun?smart=` , `/api/milestone` | Launch radar + milestone state |
| `GET/POST /api/chat` | Community chat (POST requires a wallet session) |
| `GET /api/auth/nonce` , `POST /api/auth/verify` , … | Wallet sign-in |
| `GET /api/site` | Public site config (contract address, brand) |
