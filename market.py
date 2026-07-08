"""
GeckoTerminal market-data client (free, NO API key required).

Helius has no "trending tokens" surface, so GeckoTerminal (CoinGecko's on-chain API)
fills that gap: trending Solana pools, with the base-token metadata we need
(symbol, name, logo, price, market cap, liquidity, 24h volume / change) AND the AMM
pool address that Helius then reads swaps from.

Docs: https://www.geckoterminal.com/dex-api  (rate limit ~30 calls/min on the free tier)
"""

import os
import json
import time
import threading

try:
    import requests
    _SESSION = requests.Session()
    _HTTP = "requests"
except Exception:                       # pragma: no cover
    import urllib.request
    import urllib.parse
    _SESSION = None
    _HTTP = "urllib"

BASE = "https://api.geckoterminal.com/api/v2"
HEADERS = {"accept": "application/json;version=20230302",
           "User-Agent": "solana-smart-money/1.0"}
NETWORK = "solana"
WSOL = "So11111111111111111111111111111111111111112"

TIMEOUT = float(os.environ.get("GT_TIMEOUT", "20"))
RPS = float(os.environ.get("GT_RPS", "2"))              # keep under 30/min
_MIN_INTERVAL = 1.0 / RPS if RPS > 0 else 0.0

_rate_lock = threading.Lock()
_last_call = [0.0]
_cache = {}
_cache_lock = threading.Lock()


def _throttle():
    with _rate_lock:
        wait = _MIN_INTERVAL - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()


def _get(path, params=None, ttl=120, retries=3):
    ck = path + json.dumps(params or {}, sort_keys=True)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(ck)
        if hit and now - hit[0] < ttl:
            return hit[1]

    url = BASE + path
    backoff, last_err = 0.8, None
    for _ in range(retries):
        _throttle()
        try:
            if _HTTP == "requests":
                r = _SESSION.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
                if r.status_code == 429 or r.status_code >= 500:
                    last_err = f"http {r.status_code}"
                    time.sleep(backoff); backoff *= 2; continue
                r.raise_for_status()
                data = r.json()
            else:
                full = url + ("?" + urllib.parse.urlencode(params) if params else "")
                req = urllib.request.Request(full, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            with _cache_lock:
                _cache[ck] = (time.time(), data)
            return data
        except Exception as e:
            last_err = type(e).__name__
            time.sleep(backoff); backoff *= 2
    return {"_error": last_err or "request_failed"}


def _num(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _mint_from_id(tid):
    # token ids look like "solana_<mint>"
    if isinstance(tid, str) and "_" in tid:
        return tid.split("_", 1)[1]
    return tid


def trending_pools(pages=2):
    """
    Return a normalized list of trending Solana pools.

    Each item: pool_address, base_mint, symbol, name, logo, price, price_change_24h,
    mcap, liquidity, volume_24h, sol_price (when the pool is SOL-quoted).
    """
    return _collect_pools(f"/networks/{NETWORK}/trending_pools", pages)


def top_pools(pages=5):
    """Top pools chain-wide by 24h volume — the rest of the actively-traded universe."""
    return _collect_pools(f"/networks/{NETWORK}/pools", pages,
                          extra={"sort": "h24_volume_usd_desc"})


def _collect_pools(path, pages, extra=None):
    out = []
    sol_price = 0.0
    for page in range(1, pages + 1):
        params = {"include": "base_token,quote_token", "page": page}
        if extra:
            params.update(extra)
        d = _get(path, params, ttl=120)
        if not isinstance(d, dict) or d.get("_error"):
            if out:
                break               # keep what we already collected
            return {"_error": d.get("_error") if isinstance(d, dict) else "bad"}

        # index included tokens by id
        tok = {}
        for inc in d.get("included", []):
            if inc.get("type") == "token":
                tok[inc.get("id")] = inc.get("attributes", {})

        rows = d.get("data", [])
        for p in rows:
            a = p.get("attributes", {})
            rel = p.get("relationships", {})
            base_id = (rel.get("base_token", {}).get("data") or {}).get("id")
            quote_id = (rel.get("quote_token", {}).get("data") or {}).get("id")
            bt = tok.get(base_id, {})

            quote_usd = _num(a.get("quote_token_price_usd"))
            if quote_id and _mint_from_id(quote_id) == WSOL and quote_usd > 0:
                sol_price = quote_usd

            out.append({
                "pool_address": a.get("address"),
                "base_mint": _mint_from_id(base_id),
                "symbol": (bt.get("symbol") or a.get("name", "?").split("/")[0]).strip(),
                "name": bt.get("name") or a.get("name") or "?",
                "logo": bt.get("image_url") or "",
                "price": _num(a.get("base_token_price_usd")),
                "price_change_24h": _num((a.get("price_change_percentage") or {}).get("h24")),
                "mcap": _num(a.get("market_cap_usd")) or _num(a.get("fdv_usd")),
                "liquidity": _num(a.get("reserve_in_usd")),
                "volume_24h": _num((a.get("volume_usd") or {}).get("h24")),
            })
        if not rows:
            break                   # ran past the last page

    for p in out:
        p["sol_price"] = sol_price
    return out


def sol_price():
    """Spot SOL price in USD (fallback path if no SOL-quoted trending pool seen)."""
    d = _get(f"/networks/{NETWORK}/tokens/{WSOL}", ttl=120)
    if isinstance(d, dict) and not d.get("_error"):
        return _num((d.get("data", {}).get("attributes") or {}).get("price_usd"))
    return 0.0


def token_price(mint, ttl=60):
    """USD price of any Solana token mint (for valuing gate-token holdings)."""
    d = _get(f"/networks/{NETWORK}/tokens/{mint}", ttl=ttl)
    if isinstance(d, dict) and not d.get("_error"):
        return _num((d.get("data", {}).get("attributes") or {}).get("price_usd"))
    return 0.0


def token_full(mint, ttl=60):
    """Full metadata for ANY Solana token + its highest-volume pool (across every DEX).
    Used by the search/lookup path so any contract address resolves, even if it isn't
    in the tracked top-volume set."""
    t = _get(f"/networks/{NETWORK}/tokens/{mint}", ttl=ttl)
    if not isinstance(t, dict) or t.get("_error"):
        return None
    a = (t.get("data", {}).get("attributes") or {})
    if not a.get("address"):
        return None
    out = {
        "base_mint": mint,
        "symbol": (a.get("symbol") or "?").strip(),
        "name": a.get("name") or a.get("symbol") or "?",
        "logo": a.get("image_url") or "",
        "price": _num(a.get("price_usd")),
        "price_change_24h": 0.0,
        "mcap": _num(a.get("market_cap_usd")) or _num(a.get("fdv_usd")),
        "liquidity": _num(a.get("total_reserve_in_usd")),
        "volume_24h": _num((a.get("volume_usd") or {}).get("h24")),
        "pool_address": "", "sol_price": 0.0,
    }
    pl = _get(f"/networks/{NETWORK}/tokens/{mint}/pools", {"page": 1}, ttl=ttl)
    if isinstance(pl, dict) and not pl.get("_error"):
        pools = pl.get("data", [])
        best, best_vol = None, -1.0
        for p in pools:
            pa = p.get("attributes", {})
            v = _num((pa.get("volume_usd") or {}).get("h24"))
            if v > best_vol:
                best, best_vol = pa, v
        if best:
            out["pool_address"] = best.get("address") or ""
            out["liquidity"] = out["liquidity"] or _num(best.get("reserve_in_usd"))
            out["price_change_24h"] = _num((best.get("price_change_percentage") or {}).get("h24"))
    return out


def token_mcap(mint, ttl=60):
    """USD market cap (falls back to FDV) of a token — drives roadmap milestones."""
    if not mint:
        return 0.0
    d = _get(f"/networks/{NETWORK}/tokens/{mint}", ttl=ttl)
    if isinstance(d, dict) and not d.get("_error"):
        a = (d.get("data", {}).get("attributes") or {})
        return _num(a.get("market_cap_usd")) or _num(a.get("fdv_usd"))
    return 0.0


def pool_ohlcv(pool_address, timeframe="minute", aggregate=15, limit=96):
    """Close-price series for a sparkline (oldest -> newest)."""
    d = _get(f"/networks/{NETWORK}/pools/{pool_address}/ohlcv/{timeframe}",
             {"aggregate": aggregate, "limit": limit}, ttl=180)
    if not isinstance(d, dict) or d.get("_error"):
        return []
    rows = (d.get("data", {}).get("attributes") or {}).get("ohlcv_list") or []
    rows = sorted(rows, key=lambda r: r[0])             # ts asc
    return [r[4] for r in rows if len(r) >= 5 and r[4] is not None]
