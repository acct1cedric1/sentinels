"""
Pump.fun launch radar.

Pulls the freshest pump.fun launches (newest-first, INCLUDING tokens still on the
bonding curve that haven't migrated/bonded yet — i.e. seconds old) from the public
pump.fun frontend API, and optionally filters to only those where a known
smart-money wallet has already aped in (detected via Helius swaps on the coin's
bonding-curve account).

No API key needed for the launch feed; the smart-money cross-reference reuses Helius.
"""

import time
import json
import threading

try:
    import requests
    _S = requests.Session()
    _HTTP = "requests"
except Exception:                       # pragma: no cover
    import urllib.request
    import urllib.parse
    _S = None
    _HTTP = "urllib"

import helius

PF_BASE = "https://frontend-api-v3.pump.fun"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "application/json", "Origin": "https://pump.fun",
           "Referer": "https://pump.fun/"}
TIMEOUT = 12

_throttle_lock = threading.Lock()
_last = [0.0]
_MIN_INTERVAL = 0.4
_cache = {}
_cache_lock = threading.Lock()


def _throttle():
    with _throttle_lock:
        w = _MIN_INTERVAL - (time.time() - _last[0])
        if w > 0:
            time.sleep(w)
        _last[0] = time.time()


def _get(path, ttl=6, retries=2):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(path)
        if hit and now - hit[0] < ttl:
            return hit[1]
    url = PF_BASE + path
    last_err = None
    for _ in range(retries):
        _throttle()
        try:
            if _HTTP == "requests":
                r = _S.get(url, headers=HEADERS, timeout=TIMEOUT)
                if r.status_code >= 500:
                    last_err = f"http {r.status_code}"; time.sleep(0.6); continue
                r.raise_for_status()
                data = r.json()
            else:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            with _cache_lock:
                _cache[path] = (time.time(), data)
            return data
        except Exception as e:
            last_err = str(e); time.sleep(0.6)
    return {"_error": last_err or "request_failed"}


def _num(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def newest_coins(limit=48):
    """Freshest pump.fun launches, newest first (includes un-bonded)."""
    d = _get(f"/coins?offset=0&limit={min(limit,100)}&sort=created_timestamp"
             f"&order=DESC&includeNsfw=true", ttl=5)
    if isinstance(d, dict):
        return {"_error": d.get("_error", "bad_response")}
    if not isinstance(d, list):
        return []
    now = time.time()
    out = []
    for c in d:
        created = _num(c.get("created_timestamp")) / 1000.0
        bonded = bool(c.get("complete"))
        sol_raised = _num(c.get("real_sol_reserves")) / 1e9     # lamports -> SOL
        # pump.fun graduates a curve at ~85 SOL collected
        bond_pct = 100.0 if bonded else max(0.0, min(99.0, sol_raised / 85.0 * 100))
        out.append({
            "mint": c.get("mint"),
            "symbol": c.get("symbol") or "?",
            "name": c.get("name") or c.get("symbol") or "?",
            "image": c.get("image_uri") or "",
            "created_ms": int(_num(c.get("created_timestamp"))),
            "age_s": max(0, int(now - created)) if created else None,
            "usd_mcap": _num(c.get("usd_market_cap")),
            "bonded": bonded,
            "sol_raised": round(sol_raised, 2),
            "bond_pct": round(bond_pct, 1),
            "bonding_curve": c.get("bonding_curve"),
            "creator": c.get("creator"),
            "replies": int(_num(c.get("reply_count"))),
            "last_trade_ms": int(_num(c.get("last_trade_timestamp"))),
            "twitter": c.get("twitter"), "website": c.get("website"),
        })
    return out


def coin_buyers(bonding_curve, mint, limit=30):
    """Set of wallets that BOUGHT this coin recently (via Helius bonding-curve swaps)."""
    if not bonding_curve or not mint:
        return set()
    swaps = helius.pool_swaps(bonding_curve, limit)
    if isinstance(swaps, dict) or not swaps:
        return set()
    buyers = set()
    for tx in swaps:
        w = tx.get("feePayer")
        if not w:
            continue
        recv = 0.0
        for tr in tx.get("tokenTransfers", []):
            if tr.get("mint") != mint:
                continue
            amt = _num(tr.get("tokenAmount"))
            if tr.get("toUserAccount") == w:
                recv += amt
            elif tr.get("fromUserAccount") == w:
                recv -= amt
        if recv > 0:
            buyers.add(w)
    return buyers
