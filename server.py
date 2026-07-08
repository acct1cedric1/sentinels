"""
Solana Smart Money tracker - backend server + discovery engine.

A clean-room, working tracker inspired by Nansen's "Smart Money" view, focused on
Solana memecoins. It does NOT use Nansen's proprietary wallet labels. Instead it
*auto-discovers* smart money from real on-chain swaps:

    1. GeckoTerminal (free, no key) -> trending Solana memecoin pools + token metadata.
    2. Helius (free key) -> for each pool, pull recent parsed SWAP transactions; each
       swap's `feePayer` is the trader. We compute, per wallet, how much they BOUGHT vs
       SOLD (USD, valued off the SOL / USDC / USDT leg of the swap).
    3. Treat those active traders as the smart-money cohort and aggregate, per token:
         - how many smart wallets are active in it
         - total smart BUY usd vs smart SELL usd  ->  NET flow (accumulating / distributing)
       and, per wallet, a leaderboard across every memecoin they touched.

So the board answers: "what are the most active wallets buying right now?" - here,
specifically for Solana memecoins, straight from parsed swaps.

Run:  python server.py   ->  http://localhost:8000  (start.bat uses 8050)
"""

import os
import sys
import re
import json
import time
import hashlib
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import helius
import market
import auth
import pumpfun
import chat
import trade

# Windows consoles default to cp1252 and choke on non-Latin-1 glyphs in prints.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
PORT = int(os.environ.get("PORT", "8000"))

# Tunables (env-overridable)
MAX_TOKENS = int(os.environ.get("SM_MAX_TOKENS", "100"))      # most-active tokens to scan / refresh
MAX_WALLETS = int(os.environ.get("SM_MAX_WALLETS", "120"))    # wallet leaderboard size
WORKERS = int(os.environ.get("SM_WORKERS", "4"))
TTL = int(os.environ.get("SM_TTL", "300"))                    # seconds
MIN_TRADE_USD = float(os.environ.get("SM_MIN_TRADE_USD", "10"))  # ignore dust swaps
SOL_PRICE_FALLBACK = float(os.environ.get("SM_SOL_PRICE", "150"))
MAX_BODY_BYTES = int(os.environ.get("SM_MAX_BODY_BYTES", "16384"))

# Win-rate engine: compute realized PnL win-rate for the top-N wallets by volume.
WINRATE_TOP_N = int(os.environ.get("SM_WINRATE_N", "22"))      # wallets to score / refresh
WINRATE_LOOKBACK = int(os.environ.get("SM_WINRATE_LOOKBACK", "100"))  # swaps per wallet
WINRATE_TTL = int(os.environ.get("SM_WINRATE_TTL", "1800"))   # per-wallet cache (30 min)
MIN_CLOSED = int(os.environ.get("SM_MIN_CLOSED", "3"))        # min closed trades to rank by win-rate

# How many recent swaps to scan per pool, by selected timeframe (rough proxy window).
TF_LIMIT = {"1h": 60, "4h": 90, "8h": 100, "24h": 100}

# Quote mints used to value a swap in USD.
WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"

# Tokens that are NOT memecoins (majors / stables / LSTs).
EXCLUDE_MINTS = {WSOL, USDC, USDT,
                 "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
                 "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj",
                 "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",
                 "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"}
EXCLUDE_SYMBOLS = {"SOL", "WSOL", "USDC", "USDT", "USDH", "UXD", "PYUSD", "USDY",
                   "MSOL", "JITOSOL", "BSOL", "JUPSOL", "STSOL", "INF", "JLP", "JUP",
                   "WBTC", "WETH", "CBBTC", "ETH", "BTC", "USD"}

# Deterministic generated nicknames (NOT real identities), Nansen-style flavor.
_ADJ = ["Silent", "Golden", "Rapid", "Crimson", "Lucky", "Iron", "Cosmic", "Feral",
        "Hidden", "Solar", "Frost", "Neon", "Midnight", "Turbo", "Phantom", "Diamond",
        "Savage", "Quiet", "Electric", "Rogue", "Alpha", "Mystic", "Velvet", "Atomic"]
_ANI = ["Whale", "Fox", "Shark", "Falcon", "Wolf", "Otter", "Mantis", "Lynx",
        "Cobra", "Heron", "Bison", "Hawk", "Orca", "Raven", "Panther", "Badger",
        "Marlin", "Jaguar", "Stingray", "Viper", "Kraken", "Condor", "Gecko", "Moray"]


def alias_for(addr):
    h = int(hashlib.sha1(addr.encode()).hexdigest(), 16)
    return f"{_ADJ[h % len(_ADJ)]} {_ANI[(h // 97) % len(_ANI)]}"


def short(addr):
    return addr[:4] + "…" + addr[-4:] if addr and len(addr) > 10 else addr


def is_memecoin(p):
    if p.get("base_mint") in EXCLUDE_MINTS:
        return False
    if (p.get("symbol") or "").upper() in EXCLUDE_SYMBOLS:
        return False
    return True


def _num(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _net_label(buy, sell):
    if buy + sell <= 0:
        return "No flow"
    ratio = (buy - sell) / (buy + sell)
    if ratio > 0.15:
        return "Accumulating"
    if ratio < -0.15:
        return "Distributing"
    return "Mixed"


# ---------------------------------------------------------------------------
# Swap parsing
# ---------------------------------------------------------------------------
def _swap_for_wallet(tx, mint, sol_price):
    """
    From one parsed Helius swap, return (wallet, side, usd) for the feePayer's trade in
    `mint`, or None. side is +1 (buy mint) / -1 (sell mint). usd is the SOL/stable leg.
    """
    w = tx.get("feePayer")
    if not w:
        return None

    base_net = 0.0          # +received mint / -sent mint
    sol_net = 0.0           # WSOL token + native SOL, signed for wallet
    usdc_net = 0.0
    usdt_net = 0.0

    for tr in tx.get("tokenTransfers", []):
        amt = _num(tr.get("tokenAmount"))
        if amt == 0:
            continue
        m = tr.get("mint")
        to_w = tr.get("toUserAccount") == w
        fr_w = tr.get("fromUserAccount") == w
        if not (to_w or fr_w):
            continue
        sign = 1.0 if to_w else -1.0
        if m == mint:
            base_net += sign * amt
        elif m == WSOL:
            sol_net += sign * amt
        elif m == USDC:
            usdc_net += sign * amt
        elif m == USDT:
            usdt_net += sign * amt

    for nt in tx.get("nativeTransfers", []):
        amt = _num(nt.get("amount")) / 1e9
        if nt.get("toUserAccount") == w:
            sol_net += amt
        elif nt.get("fromUserAccount") == w:
            sol_net -= amt

    if base_net == 0:
        return None
    usd = abs(sol_net) * sol_price + abs(usdc_net) + abs(usdt_net)
    if usd < MIN_TRADE_USD:
        return None
    return w, (1 if base_net > 0 else -1), usd


# ---------------------------------------------------------------------------
# Win-rate engine — realized PnL from a wallet's own recent swap history
# ---------------------------------------------------------------------------
_wr_cache = {}
_wr_lock = threading.Lock()


def _wallet_trade_leg(tx, w, sol_price):
    """For one swap, return (mint, signed_base_qty, usd) for wallet w's trade, or None.

    signed_base_qty > 0 = wallet received the token (buy), < 0 = sent it (sell).
    `mint` is the non-quote token with the largest absolute movement.
    """
    base = {}
    sol_net = usdc_net = usdt_net = 0.0
    for tr in tx.get("tokenTransfers", []):
        amt = _num(tr.get("tokenAmount"))
        if amt == 0:
            continue
        m = tr.get("mint")
        to_w = tr.get("toUserAccount") == w
        fr_w = tr.get("fromUserAccount") == w
        if not (to_w or fr_w):
            continue
        sign = 1.0 if to_w else -1.0
        if m == WSOL:
            sol_net += sign * amt
        elif m == USDC:
            usdc_net += sign * amt
        elif m == USDT:
            usdt_net += sign * amt
        else:
            base[m] = base.get(m, 0.0) + sign * amt
    for nt in tx.get("nativeTransfers", []):
        amt = _num(nt.get("amount")) / 1e9
        if nt.get("toUserAccount") == w:
            sol_net += amt
        elif nt.get("fromUserAccount") == w:
            sol_net -= amt

    base = {m: q for m, q in base.items() if q != 0}
    if not base:
        return None
    mint = max(base, key=lambda k: abs(base[k]))
    usd = abs(sol_net) * sol_price + abs(usdc_net) + abs(usdt_net)
    if usd < MIN_TRADE_USD:
        return None
    return mint, base[mint], usd


def _compute_winrate(swaps, sol_price):
    """Average-cost realized PnL across a wallet's swap history -> win-rate."""
    swaps = sorted(swaps, key=lambda t: t.get("timestamp", 0))   # oldest first
    pos = {}                  # mint -> {"qty":, "cost":}
    wins = losses = 0
    realized = 0.0
    for tx in swaps:
        leg = _wallet_trade_leg(tx, tx.get("feePayer"), sol_price)
        if not leg:
            continue
        mint, qty, usd = leg
        p = pos.setdefault(mint, {"qty": 0.0, "cost": 0.0})
        if qty > 0:                                  # buy
            p["qty"] += qty
            p["cost"] += usd
        else:                                        # sell
            if p["qty"] <= 0:
                continue                             # position opened before window
            sell_qty = min(-qty, p["qty"])
            avg = p["cost"] / p["qty"]
            proceeds = usd * (sell_qty / (-qty))
            pnl = proceeds - avg * sell_qty
            realized += pnl
            if pnl > 0:
                wins += 1
            else:
                losses += 1
            p["qty"] -= sell_qty
            p["cost"] -= avg * sell_qty
    closed = wins + losses
    return {"winrate": round(wins / closed * 100, 1) if closed else None,
            "wins": wins, "losses": losses, "closed_trades": closed,
            "realized_pnl": round(realized, 2)}


def _wallet_winrate(address, sol_price):
    now = time.time()
    with _wr_lock:
        hit = _wr_cache.get(address)
        if hit and now - hit[0] < WINRATE_TTL:
            return hit[1]
    swaps = helius.wallet_swaps(address, limit=WINRATE_LOOKBACK)
    res = _compute_winrate(swaps, sol_price)
    with _wr_lock:
        _wr_cache[address] = (time.time(), res)
    return res


def _scan_pool_raw(p, limit, sol_price):
    """Scan a pool's recent swaps ONCE into raw per-swap records (kept with timestamps
    so any timeframe can be derived later without new API calls)."""
    swaps = helius.pool_swaps(p["pool_address"], limit)
    if isinstance(swaps, dict):
        err = swaps.get("_error", "bad")
        return p, ([] if err == "http_404" else None)   # None = real error, [] = empty
    if not swaps:
        return p, []
    mint = p["base_mint"]
    recs = []
    for tx in swaps:
        res = _swap_for_wallet(tx, mint, sol_price)
        if not res:
            continue
        w, side, usd = res
        recs.append((w, side, usd, int(_num(tx.get("timestamp")))))
    return p, recs


TF_SECONDS = {"1h": 3600, "4h": 14400, "8h": 28800, "24h": 86400}


def _aggregate_token(meta, recs, cutoff):
    """Fold a token's raw swap records (within `cutoff`) into a token row + wallet rows."""
    per_wallet = {}
    for w, side, usd, ts in recs:
        if ts and cutoff and ts < cutoff:
            continue
        rec = per_wallet.setdefault(w, {"buy": 0.0, "sell": 0.0, "trades": 0})
        rec["trades"] += 1
        if side > 0:
            rec["buy"] += usd
        else:
            rec["sell"] += usd
    if not per_wallet:
        return None, []

    buy = sum(r["buy"] for r in per_wallet.values())
    sell = sum(r["sell"] for r in per_wallet.values())
    tr_rows = []
    for w, r in per_wallet.items():
        tr_rows.append({
            "wallet": w, "alias": alias_for(w), "short": short(w),
            "buy_usd": round(r["buy"], 2), "sell_usd": round(r["sell"], 2),
            "net_usd": round(r["buy"] - r["sell"], 2),
            "volume_usd": round(r["buy"] + r["sell"], 2), "trades": r["trades"],
        })
    tr_rows.sort(key=lambda r: r["volume_usd"], reverse=True)

    token_row = {
        "address": meta["base_mint"], "pool": meta["pool_address"],
        "symbol": meta.get("symbol") or "?",
        "name": meta.get("name") or meta.get("symbol") or "?",
        "logo": meta.get("logo") or "",
        "price": meta.get("price", 0.0),
        "price_change_24h": meta.get("price_change_24h", 0.0),
        "mcap": meta.get("mcap", 0.0), "liquidity": meta.get("liquidity", 0.0),
        "volume_24h": meta.get("volume_24h", 0.0),
        "smart_wallets": len(tr_rows),
        "smart_buy_usd": round(buy, 2), "smart_sell_usd": round(sell, 2),
        "smart_net_usd": round(buy - sell, 2), "smart_volume_usd": round(buy + sell, 2),
        "net_label": _net_label(buy, sell), "top_traders": tr_rows[:10],
    }
    return token_row, tr_rows


# ---------------------------------------------------------------------------
# Build — one expensive base scan, then instant in-memory derivations
# ---------------------------------------------------------------------------
_CACHE = {}
_LOCK = threading.Lock()
_MINT_POOL = {}          # mint -> pool address (for the token drawer)
_BASE = {"ts": 0, "data": None}
_BASE_LOCK = threading.Lock()
_BASE_TTL = int(os.environ.get("SM_BASE_TTL", "300"))
_WARMUP = {"running": False, "last_error": None, "last_ok": None}
_WARMUP_LOCK = threading.Lock()


class RequestBodyError(Exception):
    def __init__(self, code, error):
        super().__init__(error)
        self.code = code
        self.error = error


def _err_payload(err, tf, memes):
    return {"error": err, "tokens": [], "wallets": [], "updated": "—",
            "token_count": 0, "wallet_count": 0, "time_frame": tf,
            "memecoins_only": memes}


def build_base(force=False):
    """Discover the actively-traded Solana universe and scan each pool's recent swaps
    ONCE. Cached; timeframe/memecoins views are derived from this with zero API calls."""
    now = time.time()
    with _BASE_LOCK:
        if not force and _BASE["data"] is not None and now - _BASE["ts"] < _BASE_TTL:
            return _BASE["data"]

    trend = market.trending_pools(pages=5)
    if isinstance(trend, dict):
        trend = []
    top = market.top_pools(pages=5)
    if isinstance(top, dict):
        top = []
    pools = trend + top
    if not pools:
        return {"_error": "market_unavailable"}

    sol_price = next((p["sol_price"] for p in pools if p.get("sol_price")), 0.0)
    if sol_price <= 0:
        sol_price = market.sol_price() or SOL_PRICE_FALLBACK

    # one row per token: keep its highest-volume pool, then rank by 24h volume
    best = {}
    for p in pools:
        m = p.get("base_mint")
        if not m or not p.get("pool_address"):
            continue
        cur = best.get(m)
        if cur is None or p.get("volume_24h", 0) > cur.get("volume_24h", 0):
            best[m] = p
    toks = sorted(best.values(), key=lambda p: p.get("volume_24h", 0), reverse=True)[:MAX_TOKENS]

    limit = max(TF_LIMIT.values())
    tokens, scan_err = [], None
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for meta, recs in ex.map(lambda t: _scan_pool_raw(t, limit, sol_price), toks):
            _MINT_POOL[meta["base_mint"]] = meta["pool_address"]
            if recs is None:
                scan_err = scan_err or "scan_partial"
                continue
            tokens.append({"meta": meta, "recs": recs})

    # Warm the win-rate cache for the most active wallets across the WHOLE window now,
    # so every timeframe/memecoin derivation afterwards is instant (zero API calls).
    vol = {}
    for t in tokens:
        for w, side, usd, ts in t["recs"]:
            vol[w] = vol.get(w, 0.0) + usd
    warm = [w for w, _ in sorted(vol.items(), key=lambda x: x[1], reverse=True)[:WINRATE_TOP_N + 25]]
    if warm:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            list(ex.map(lambda x: _wallet_winrate(x, sol_price), warm))

    data = {"sol_price": sol_price, "tokens": tokens, "scan_err": scan_err,
            "updated": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}
    with _BASE_LOCK:
        _BASE["ts"] = time.time()
        _BASE["data"] = data
    return data


def build_smart_money(time_frame="24h", memecoins_only=False, force=False):
    ck = f"{time_frame}|{int(memecoins_only)}"
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(ck)
        if hit and not force and now - hit[0] < TTL:
            return hit[1]

    if not helius.has_key():
        return _err_payload("no_api_key", time_frame, memecoins_only)

    base = build_base(force=force)
    if isinstance(base, dict) and base.get("_error"):
        return _err_payload(base["_error"], time_frame, memecoins_only)

    sol_price = base["sol_price"]
    cutoff = int(now - TF_SECONDS.get(time_frame, 86400))

    token_rows, wallet_agg = [], {}
    for t in base["tokens"]:
        meta = t["meta"]
        if memecoins_only and not is_memecoin(meta):
            continue
        token_row, tr_rows = _aggregate_token(meta, t["recs"], cutoff)
        if not token_row:
            continue
        token_rows.append(token_row)
        for r in tr_rows:
            w = r["wallet"]
            wa = wallet_agg.setdefault(w, {
                "wallet": w, "alias": r["alias"], "short": r["short"],
                "buy_usd": 0.0, "sell_usd": 0.0, "volume_usd": 0.0,
                "trades": 0, "tokens": []})
            wa["buy_usd"] += r["buy_usd"]
            wa["sell_usd"] += r["sell_usd"]
            wa["volume_usd"] += r["volume_usd"]
            wa["trades"] += r["trades"]
            wa["tokens"].append({"symbol": token_row["symbol"],
                                 "address": token_row["address"], "net_usd": r["net_usd"]})

    token_rows.sort(key=lambda r: r["smart_net_usd"], reverse=True)

    wallets = []
    for wa in wallet_agg.values():
        wa["net_usd"] = round(wa["buy_usd"] - wa["sell_usd"], 2)
        wa["buy_usd"] = round(wa["buy_usd"], 2)
        wa["sell_usd"] = round(wa["sell_usd"], 2)
        wa["volume_usd"] = round(wa["volume_usd"], 2)
        wa["tokens_traded"] = len(wa["tokens"])
        wa["tokens"].sort(key=lambda t: abs(t["net_usd"]), reverse=True)
        wa["top_tokens"] = [t["symbol"] for t in wa["tokens"][:6]]
        wa["winrate"] = None
        wa["closed_trades"] = 0
        wa["realized_pnl"] = None
        wallets.append(wa)
    wallets.sort(key=lambda w: w["volume_usd"], reverse=True)
    wallets = wallets[:MAX_WALLETS]

    # Score win-rate for the most active wallets (realized PnL from their own history;
    # tf-independent, cached 30 min per wallet — so derivations stay fast).
    scored = wallets[:WINRATE_TOP_N]
    if scored:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for w, wr in zip(scored, ex.map(
                    lambda x: _wallet_winrate(x["wallet"], sol_price), scored)):
                w.update(wr)

    def _wr_key(w):
        ranked = w.get("winrate") is not None and w.get("closed_trades", 0) >= MIN_CLOSED
        return (1 if ranked else 0, w["winrate"] if ranked else -1.0, w["volume_usd"])
    wallets.sort(key=_wr_key, reverse=True)

    payload = {
        "updated": base["updated"],
        "time_frame": time_frame,
        "memecoins_only": memecoins_only,
        "sol_price": round(sol_price, 2),
        "token_count": len(token_rows),
        "wallet_count": len(wallets),
        "tokens": token_rows,
        "wallets": wallets,
        "partial_error": base.get("scan_err"),
    }
    with _LOCK:
        _CACHE[ck] = (time.time(), payload)
    return payload


# ---------------------------------------------------------------------------
# Detail endpoints
# ---------------------------------------------------------------------------
def token_detail(address, time_frame="24h"):
    pool = _MINT_POOL.get(address)
    spark = market.pool_ohlcv(pool) if pool else []
    return {"address": address, "pool": pool, "spark": spark}


_B58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def lookup_token(address, time_frame="24h"):
    """Resolve ANY Solana token by mint, even if it's not in the tracked set or has
    zero smart-money activity. Returns a single token row in the standard shape."""
    address = (address or "").strip()
    if not _B58_RE.match(address):
        return {"error": "not_an_address"}
    meta = market.token_full(address)
    if not meta:
        return {"error": "not_found"}

    sol_price = (build_base().get("sol_price") if helius.has_key() else 0) or SOL_PRICE_FALLBACK
    recs = []
    if meta.get("pool_address") and helius.has_key():
        _MINT_POOL[address] = meta["pool_address"]
        _, raw = _scan_pool_raw(meta, max(TF_LIMIT.values()), sol_price)
        recs = raw or []
    cutoff = int(time.time() - TF_SECONDS.get(time_frame, 86400))
    token_row, _ = _aggregate_token(meta, recs, cutoff)
    if not token_row:
        # no smart-money flow — still surface the token with zeroed flow
        token_row = {
            "address": address, "pool": meta.get("pool_address", ""),
            "symbol": meta.get("symbol") or "?", "name": meta.get("name") or "?",
            "logo": meta.get("logo") or "", "price": meta.get("price", 0.0),
            "price_change_24h": meta.get("price_change_24h", 0.0),
            "mcap": meta.get("mcap", 0.0), "liquidity": meta.get("liquidity", 0.0),
            "volume_24h": meta.get("volume_24h", 0.0),
            "smart_wallets": 0, "smart_buy_usd": 0.0, "smart_sell_usd": 0.0,
            "smart_net_usd": 0.0, "smart_volume_usd": 0.0,
            "net_label": "No smart-money activity", "top_traders": [],
            "no_smart": True,
        }
    return {"token": token_row}


def wallet_detail(address):
    assets = helius.assets_by_owner(address)
    holds = []
    for a in assets:
        if a.get("interface") not in ("FungibleToken", "FungibleAsset", "Token", None):
            continue
        ti = a.get("token_info") or {}
        pi = ti.get("price_info") or {}
        val = _num(pi.get("total_price"))
        if val <= 0:
            continue
        meta = (a.get("content") or {}).get("metadata") or {}
        holds.append({"symbol": ti.get("symbol") or meta.get("symbol") or "?",
                      "name": meta.get("name"), "value_usd": round(val, 2),
                      "address": a.get("id")})
    holds.sort(key=lambda h: h["value_usd"], reverse=True)
    swaps = helius.wallet_swaps(address, limit=20)
    return {"wallet": address, "alias": alias_for(address), "short": short(address),
            "holdings": holds[:25], "trades_available": bool(swaps)}


def _warmup_background():
    with _WARMUP_LOCK:
        _WARMUP.update({"running": True, "last_error": None})
    try:
        d = build_smart_money(force=True)
        with _WARMUP_LOCK:
            _WARMUP["running"] = False
            _WARMUP["last_ok"] = time.time()
            _WARMUP["last_error"] = d.get("error")
        if d.get("error"):
            print(f"  (warmup error: {d['error']})")
        else:
            print(f"  Tracking {d['wallet_count']} smart wallets across "
                  f"{d['token_count']} tokens. SOL ${d.get('sol_price')}. {d['updated']}")
    except Exception as e:
        with _WARMUP_LOCK:
            _WARMUP["running"] = False
            _WARMUP["last_error"] = str(e)
        print(f"  (warmup failed, will retry on request: {e})")


# ---------------------------------------------------------------------------
# Token-gating: per-request redaction view (full data is never sent to free users)
# ---------------------------------------------------------------------------
def gate_view(payload, premium):
    """Return a copy of the board with high-win-rate wallet addresses redacted
    for non-premium users. Win rates stay visible; only identity is gated."""
    g = auth.gating()
    lock = g["lock_winrate"]
    redact = g["enabled"] and not premium
    out = dict(payload)
    locked = 0
    new_wallets = []
    for w in payload.get("wallets", []):
        w2 = dict(w)
        gated = bool(redact and w.get("winrate") is not None and w["winrate"] >= lock)
        if gated:
            w2["wallet"] = ""           # real address withheld server-side
            w2["short"] = ""
            w2["tokens"] = []           # hide position detail too
            w2["top_tokens"] = []       # hide which coins they're trading (that's the alpha)
            w2["gated"] = True
            locked += 1
        else:
            w2["gated"] = False
        new_wallets.append(w2)
    out["wallets"] = new_wallets
    out["gating"] = {
        "active": redact, "premium": premium, "enabled": g["enabled"],
        "lock_winrate": lock, "locked": locked,
        "token_set": bool(g["token_mint"]), "min_usd": g["min_usd"],
    }
    return out


# ---------------------------------------------------------------------------
# Pump.fun launch radar — milestone-gated ($100K project mcap auto-release)
# ---------------------------------------------------------------------------
_PF_CACHE = {}
_PF_LOCK = threading.Lock()
PF_SMART_TTL = int(os.environ.get("SM_PF_SMART_TTL", "30"))   # smart-scan cache, s
PF_SMART_SCAN = int(os.environ.get("SM_PF_SMART_SCAN", "18")) # newest coins to cross-check


def pumpfun_milestone(session=None):
    """Auto-release check: unlocked for everyone once the project token's
    market cap reaches the target. Admin wallets can preview before launch."""
    g = auth.gating()
    try:
        target = float(g.get("pumpfun_mcap_target", 100000))
    except (TypeError, ValueError):
        target = 100000.0
    mint = (g.get("token_mint") or "").strip()
    mcap = market.token_mcap(mint) if mint else 0.0
    reached = mcap >= target          # target <= 0 force-releases the feature
    admin = bool(session and session.get("pubkey") in g["admin_wallets"])
    progress = 100.0 if reached else (round(min(100.0, mcap / target * 100), 1) if target > 0 else 0.0)
    return {"target": target, "mcap": round(mcap, 2), "token_set": bool(mint),
            "progress": progress,
            "unlocked": reached or admin, "reached": reached,
            "admin_preview": admin and not reached}


def build_pumpfun(smart_only=False, view="new"):
    """Pump.fun radar feed. Views: new (freshest launches), grad (graduating soon —
    top un-bonded curves by mcap), bonded (recently graduated). With smart_only
    (new view only), keep coins already bought by the smart-money cohort."""
    if view not in ("new", "grad", "bonded"):
        view = "new"
    if view != "new":
        smart_only = False          # cross-ref reads bonding-curve swaps; new view only
    ck = f"pf|{view}|{int(smart_only)}"
    now = time.time()
    ttl = PF_SMART_TTL if smart_only else (6 if view == "new" else 20)
    with _PF_LOCK:
        hit = _PF_CACHE.get(ck)
        if hit and now - hit[0] < ttl:
            return hit[1]

    coins = pumpfun.fetch_coins(view, 48)
    if isinstance(coins, dict):
        return {"error": coins.get("_error", "pumpfun_unavailable"), "coins": [],
                "count": 0, "smart_only": smart_only, "view": view}

    if smart_only:
        with _LOCK:
            board = _CACHE.get("24h|0") or _CACHE.get("24h|1")
        cohort = {w["wallet"] for w in (board[1]["wallets"] if board else [])}
        matched = []
        for c in coins[:PF_SMART_SCAN]:
            if not cohort:
                break
            buyers = pumpfun.coin_buyers(c.get("bonding_curve"), c.get("mint"), limit=40)
            hits = buyers & cohort
            if hits:
                c2 = dict(c)
                c2["smart_buyers"] = len(hits)
                c2["smart_aliases"] = [alias_for(w) for w in sorted(hits)[:4]]
                matched.append(c2)
        coins = matched

    payload = {"updated": datetime.datetime.utcnow().strftime("%H:%M:%S UTC"),
               "count": len(coins), "coins": coins, "smart_only": smart_only,
               "view": view}
    with _PF_LOCK:
        _PF_CACHE[ck] = (time.time(), payload)
    return payload


def site_config():
    """Public site config (contract address, brand) for the landing page."""
    cfg = {}
    try:
        with open(os.path.join(HERE, "config.json"), "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        pass
    return {"brand": "Sentinels",
            "contract_address": (cfg.get("contract_address") or "").strip(),
            "github_url": (cfg.get("github_url") or "").strip()}


def compute_balance_usd(pubkey):
    """USD value of the connected wallet's gate-token (or SOL) holding."""
    g = auth.gating()
    mint = (g["token_mint"] or "").strip()
    if not mint:
        return 0.0
    if mint.upper() == "SOL":
        bal = helius.sol_balance(pubkey)
        px = next((p["sol_price"] for p in market.trending_pools(pages=1)
                   if p.get("sol_price")), 0.0) or market.sol_price() or SOL_PRICE_FALLBACK
        return round(bal * px, 2)
    bal = helius.token_balance(pubkey, mint)
    return round(bal * market.token_price(mint), 2)


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", extra_headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        headers = extra_headers or []
        if not any(k.lower() == "x-content-type-options" for k, _ in headers):
            self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _cookies(self):
        from http.cookies import SimpleCookie
        c = SimpleCookie()
        raw = self.headers.get("Cookie", "")
        if raw:
            try:
                c.load(raw)
            except Exception:
                pass
        return {k: m.value for k, m in c.items()}

    def _session(self):
        return auth.read_session(self._cookies().get("sm_session", ""))

    def _premium(self):
        s = self._session()
        return bool(s and s.get("premium"))

    def _body_json(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            raise RequestBodyError(400, "bad_json")
        if n < 0:
            raise RequestBodyError(400, "bad_json")
        if n > MAX_BODY_BYTES:
            raise RequestBodyError(413, "request_too_large")
        if n == 0:
            return {}
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            raise RequestBodyError(400, "bad_json")
        if not isinstance(body, dict):
            raise RequestBodyError(400, "bad_json")
        return body

    @staticmethod
    def _cookie_header(token, max_age):
        return ("Set-Cookie",
                f"sm_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={max_age}")

    def _query(self):
        from urllib.parse import urlparse, parse_qs
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def _serve_static(self, path):
        if path in ("/", ""):
            path = "/landing.html"      # landing page is the front door
        elif path == "/app":
            path = "/index.html"        # the dashboard
        fp = os.path.abspath(os.path.normpath(os.path.join(STATIC_DIR, path.lstrip("/"))))
        try:
            inside_static = os.path.commonpath([STATIC_DIR, fp]) == STATIC_DIR
        except ValueError:
            inside_static = False
        if not inside_static or not os.path.isfile(fp):
            self._send(404, {"error": "not found"})
            return
        ctype = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8",
                 ".svg": "image/svg+xml"}.get(os.path.splitext(fp)[1], "application/octet-stream")
        with open(fp, "rb") as f:
            # no-cache = browsers must revalidate, so users never run stale JS after updates
            self._send(200, f.read(), ctype,
                       extra_headers=[("Cache-Control", "no-cache")])

    def do_GET(self):
        path = self.path.split("?")[0]
        q = self._query()
        try:
            if path == "/api/smartmoney":
                tf = q.get("tf", "24h")
                memes = q.get("memecoins", "0") == "1"
                full = build_smart_money(tf, memes, force=q.get("force") == "1")
                self._send(200, gate_view(full, self._premium()))
                return
            if path == "/api/token":
                self._send(200, token_detail(q.get("address", ""), q.get("tf", "24h")))
                return
            if path == "/api/lookup":
                self._send(200, lookup_token(q.get("address", ""), q.get("tf", "24h")))
                return
            if path == "/api/wallet":
                self._send(200, wallet_detail(q.get("address", "")))
                return
            if path == "/api/pumpfun":
                smart = q.get("smart", "0") == "1"
                view = q.get("view", "new")
                ms = pumpfun_milestone(self._session())
                if not ms["unlocked"]:
                    self._send(200, {"locked": True, "milestone": ms, "coins": [],
                                     "count": 0, "smart_only": smart, "view": view})
                    return
                payload = dict(build_pumpfun(smart, view))
                payload["locked"] = False
                payload["milestone"] = ms
                self._send(200, payload)
                return
            if path == "/api/trade/status":
                ok, reason = trade.unlocked(self._session())
                self._send(200, {"unlocked": ok, "reason": reason})
                return
            if path == "/api/milestone":
                self._send(200, pumpfun_milestone(self._session()))
                return
            if path == "/api/site":
                self._send(200, site_config())
                return
            if path == "/api/chat":
                after = int(q.get("after", "0") or 0)
                s = self._session()
                self._send(200, {"messages": chat.recent(after),
                                 "can_post": bool(s), "ai": chat.ai_enabled()})
                return
            if path == "/api/auth/nonce":
                nonce, message = auth.issue_nonce()
                self._send(200, {"nonce": nonce, "message": message})
                return
            if path == "/api/auth/status":
                s = self._session()
                g = auth.gating()
                self._send(200, {
                    "authenticated": bool(s), "pubkey": (s or {}).get("pubkey"),
                    "premium": bool(s and s.get("premium")), "reason": (s or {}).get("reason"),
                    "balance_usd": (s or {}).get("balance_usd"),
                    "gating": {"enabled": g["enabled"], "lock_winrate": g["lock_winrate"],
                               "min_usd": g["min_usd"], "token_set": bool(g["token_mint"])}})
                return
            if path == "/api/health":
                with _WARMUP_LOCK:
                    warm = dict(_WARMUP)
                with _BASE_LOCK:
                    base_cached = _BASE["data"] is not None
                self._send(200, {"ok": True, "key": helius.has_key(), "http": helius._HTTP,
                                 "warming": warm["running"], "base_cached": base_cached,
                                 "last_warmup_error": warm["last_error"],
                                 "last_warmup_ok": warm["last_ok"]})
                return
        except Exception as e:
            self._send(500, {"error": str(e)})
            return
        self._serve_static(path)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/auth/verify":
                b = self._body_json()
                pubkey = (b.get("pubkey") or "").strip()
                signature = b.get("signature") or ""
                nonce = b.get("nonce") or ""
                message = auth.consume_nonce(nonce)
                if not message:
                    self._send(400, {"error": "nonce expired — try again"})
                    return
                if not pubkey or not auth.verify_signature(pubkey, message, signature):
                    self._send(401, {"error": "signature verification failed"})
                    return
                balance_usd = compute_balance_usd(pubkey)
                premium, reason = auth.decide_premium(pubkey, balance_usd)
                token = auth.make_session({
                    "pubkey": pubkey, "premium": premium, "reason": reason,
                    "balance_usd": balance_usd, "exp": time.time() + auth.SESSION_TTL})
                self._send(200, {"premium": premium, "reason": reason,
                                 "balance_usd": balance_usd, "pubkey": pubkey},
                           extra_headers=[self._cookie_header(token, auth.SESSION_TTL)])
                return
            if path == "/api/auth/logout":
                self._send(200, {"ok": True},
                           extra_headers=[self._cookie_header("", 0)])
                return
            if path == "/api/chat":
                s = self._session()
                if not s or not s.get("pubkey"):
                    self._send(401, {"error": "auth", "reason": "Connect your wallet to chat."})
                    return
                pubkey = s["pubkey"]
                text = (self._body_json().get("text") or "")
                self._send(200, chat.post(pubkey, alias_for(pubkey), text))
                return
            if path in ("/api/trade/quote", "/api/trade/swap"):
                s = self._session()
                if not s or not s.get("pubkey"):
                    self._send(401, {"error": "auth", "reason": "Connect your wallet first."})
                    return
                b = self._body_json()
                self._send(200, trade.prepare(
                    s, b.get("side"), b.get("mint"),
                    sol_amount=b.get("sol_amount"), pct=b.get("pct"),
                    slippage_bps=b.get("slippage_bps", 100),
                    execute=(path == "/api/trade/swap")))
                return
        except RequestBodyError as e:
            self._send(e.code, {"error": e.error})
            return
        except Exception as e:
            self._send(500, {"error": str(e)})
            return
        self._send(404, {"error": "not found"})


def main():
    print("=" * 64)
    print("  Solana Smart Money tracker  ·  starting up")
    print("  data: Helius (swaps) + GeckoTerminal (trending, free)")
    print("=" * 64)
    if not helius.has_key():
        print("  [!] No Helius API key found.")
        print("      Set HELIUS_API_KEY, or create config.json with")
        print('      {\"helius_api_key\": \"YOUR_KEY\"}  (or api_key.txt).')
        print("      Free dev key: https://helius.dev  ->  Dashboard  ->  API Keys.")
    elif False:
        print("  Helius key loaded. Warming up (trending + swap parsing)…")
        try:
            d = build_smart_money(force=True)
            if d.get("error"):
                print(f"  (warmup error: {d['error']})")
            else:
                print(f"  Tracking {d['wallet_count']} smart wallets across "
                      f"{d['token_count']} tokens. SOL ${d.get('sol_price')}. {d['updated']}")
        except Exception as e:
            print(f"  (warmup failed, will retry on request: {e})")
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    if helius.has_key():
        print("  Helius key loaded. Warming up in the background (trending + swap parsing)...")
    threading.Thread(target=_warmup_background, daemon=True).start()
    print(f"\n  Open  ->  http://localhost:{PORT}\n  Ctrl+C to stop.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
