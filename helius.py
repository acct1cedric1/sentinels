"""
Helius API client for the Solana Smart Money tracker.

Helius is the *intelligence* layer: it parses raw Solana transactions into clean,
labeled swaps (who swapped, which tokens, how much) and exposes wallet holdings via
the DAS API. We use two surfaces:

    * Enhanced Transactions API  (https://api.helius.xyz/v0/...)
        - GET /v0/addresses/{address}/transactions?type=SWAP   -> parsed swaps that
          touched an address. Pointed at an AMM *pool* address this yields every recent
          swap in that pool, each with `feePayer` = the trader and token/native transfers.
    * RPC + DAS  (https://mainnet.helius-rpc.com/?api-key=...)
        - getAssetsByOwner (showFungible) -> a wallet's token holdings with USD value.

Key load order:  env HELIUS_API_KEY  ->  config.json {"helius_api_key": "..."}  ->  api_key.txt
Free dev keys: https://helius.dev  ->  Dashboard  ->  API Keys.

All helpers swallow network errors into {"_error": ...} / [] so the dashboard degrades
gracefully instead of crashing.
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

HERE = os.path.dirname(os.path.abspath(__file__))
RPC_URL = "https://mainnet.helius-rpc.com/"
ENH_BASE = "https://api.helius.xyz"

RPS = float(os.environ.get("HELIUS_RPS", "8"))
TIMEOUT = float(os.environ.get("HELIUS_TIMEOUT", "20"))
_MIN_INTERVAL = 1.0 / RPS if RPS > 0 else 0.0


# ---------------------------------------------------------------------------
# API key
# ---------------------------------------------------------------------------
def load_key():
    k = os.environ.get("HELIUS_API_KEY", "").strip()
    if k:
        return k
    cfg = os.path.join(HERE, "config.json")
    if os.path.isfile(cfg):
        try:
            with open(cfg, "r", encoding="utf-8") as f:
                k = (json.load(f).get("helius_api_key") or "").strip()
            if k:
                return k
        except Exception:
            pass
    txt = os.path.join(HERE, "api_key.txt")
    if os.path.isfile(txt):
        try:
            with open(txt, "r", encoding="utf-8") as f:
                k = f.read().strip()
            if k:
                return k
        except Exception:
            pass
    return ""


API_KEY = load_key()


def has_key():
    return bool(API_KEY)


# ---------------------------------------------------------------------------
# Rate-limited request with retry + TTL cache
# ---------------------------------------------------------------------------
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


def _request(method, url, params=None, body=None, ttl=60, retries=4):
    if not API_KEY:
        return {"_error": "no_api_key"}

    ck = method + url + json.dumps(params or {}, sort_keys=True) + json.dumps(body or {}, sort_keys=True)
    now = time.time()
    if ttl:
        with _cache_lock:
            hit = _cache.get(ck)
            if hit and now - hit[0] < ttl:
                return hit[1]

    headers = {"accept": "application/json", "content-type": "application/json"}
    backoff, last_err = 0.8, None
    for _ in range(retries):
        _throttle()
        try:
            if _HTTP == "requests":
                if method == "GET":
                    r = _SESSION.get(url, params=params, headers=headers, timeout=TIMEOUT)
                else:
                    r = _SESSION.post(url, params=params, json=body, headers=headers, timeout=TIMEOUT)
                code = r.status_code
                if code == 429 or code >= 500:
                    last_err = f"http {code}"
                    time.sleep(backoff); backoff *= 2; continue
                if code in (401, 403):
                    return {"_error": f"auth_{code}"}
                if code >= 400:
                    # never str(exception) here — requests puts the full URL
                    # (incl. the api-key) in HTTPError messages
                    return {"_error": f"http_{code}"}
                data = r.json()
            else:                                       # urllib fallback
                full = url
                if params:
                    full += "?" + urllib.parse.urlencode(params)
                payload = json.dumps(body).encode() if body is not None else None
                req = urllib.request.Request(full, data=payload, headers=headers,
                                             method=method)
                with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

            if ttl:
                with _cache_lock:
                    _cache[ck] = (time.time(), data)
            return data
        except Exception as e:
            # exception text can embed the request URL (incl. the api-key) — scrub it
            last_err = type(e).__name__
            time.sleep(backoff); backoff *= 2
    return {"_error": last_err or "request_failed"}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def pool_swaps(pool_address, limit=100, ttl=180):
    """Recent parsed SWAP transactions that touched a pool/market address."""
    url = f"{ENH_BASE}/v0/addresses/{pool_address}/transactions"
    d = _request("GET", url, params={"api-key": API_KEY, "type": "SWAP",
                                     "limit": min(limit, 100)}, ttl=ttl)
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        return {"_error": d.get("_error", "bad_response")}
    return []


def wallet_swaps(wallet, limit=25, ttl=120):
    """Recent parsed swaps for a single wallet."""
    url = f"{ENH_BASE}/v0/addresses/{wallet}/transactions"
    d = _request("GET", url, params={"api-key": API_KEY, "type": "SWAP",
                                     "limit": min(limit, 100)}, ttl=ttl)
    return d if isinstance(d, list) else []


def token_balance(owner, mint, ttl=30):
    """UI amount of `mint` held by `owner` (sums all token accounts)."""
    body = {"jsonrpc": "2.0", "id": "bal", "method": "getTokenAccountsByOwner",
            "params": [owner, {"mint": mint}, {"encoding": "jsonParsed"}]}
    d = _request("POST", RPC_URL, params={"api-key": API_KEY}, body=body, ttl=ttl)
    if not isinstance(d, dict) or d.get("_error"):
        return 0.0
    total = 0.0
    for acc in ((d.get("result") or {}).get("value") or []):
        try:
            info = acc["account"]["data"]["parsed"]["info"]["tokenAmount"]
            total += float(info.get("uiAmount") or 0)
        except Exception:
            pass
    return total


def sol_balance(owner, ttl=30):
    """Native SOL balance of `owner`."""
    body = {"jsonrpc": "2.0", "id": "sol", "method": "getBalance", "params": [owner]}
    d = _request("POST", RPC_URL, params={"api-key": API_KEY}, body=body, ttl=ttl)
    if isinstance(d, dict) and not d.get("_error"):
        return float((d.get("result") or {}).get("value") or 0) / 1e9
    return 0.0


def assets_by_owner(owner, ttl=120):
    """DAS getAssetsByOwner with fungible balances + USD price info."""
    body = {"jsonrpc": "2.0", "id": "sm", "method": "getAssetsByOwner",
            "params": {"ownerAddress": owner, "page": 1, "limit": 200,
                       "displayOptions": {"showFungible": True,
                                          "showNativeBalance": True}}}
    d = _request("POST", RPC_URL, params={"api-key": API_KEY}, body=body, ttl=ttl)
    if isinstance(d, dict) and not d.get("_error"):
        return (d.get("result") or {}).get("items") or []
    return []
