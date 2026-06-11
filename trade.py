"""
In-app trading engine for Sentinels (non-custodial, via Jupiter).

The server only BUILDS swap transactions — it never holds keys and never signs.
Flow:  client asks for a swap -> we fetch a Jupiter quote -> Jupiter returns an
unsigned serialized transaction for the user's pubkey -> the user's wallet signs
and sends it locally. Funds never touch the server.

Unlock policy (checked per request):
    1. wallets in gating.admin_wallets         -> always (dev preview)
    2. gating.trading_unlocked == true         -> manual override ("flip at bond")
    3. gating.token_mint set AND that coin's pump.fun curve is complete (bonded)
       -> auto-unlock for everyone

Jupiter: free keyless tier at lite-api.jup.ag (the old quote-api.jup.ag v6 host
is dead). Quote: GET /swap/v1/quote   Swap build: POST /swap/v1/swap
"""

import os
import json
import time
import threading

try:
    import requests
    _S = requests.Session()
except Exception:                       # pragma: no cover
    requests = None
    _S = None

import auth
import helius
import pumpfun

JUP = "https://lite-api.jup.ag/swap/v1"
WSOL = "So11111111111111111111111111111111111111112"
TIMEOUT = 15
MAX_SOL_PER_TRADE = float(os.environ.get("SM_MAX_TRADE_SOL", "50"))

_bond_cache = {"ts": 0, "bonded": False, "mint": ""}
_bond_lock = threading.Lock()


def _token_bonded(mint):
    """Has the project token graduated its pump.fun curve? Cached 60s."""
    now = time.time()
    with _bond_lock:
        if _bond_cache["mint"] == mint and now - _bond_cache["ts"] < 60:
            return _bond_cache["bonded"]
    bonded = False
    try:
        d = pumpfun._get(f"/coins/{mint}", ttl=60)
        if isinstance(d, dict) and not d.get("_error"):
            bonded = bool(d.get("complete"))
    except Exception:
        pass
    with _bond_lock:
        _bond_cache.update({"ts": now, "bonded": bonded, "mint": mint})
    return bonded


def unlocked(session):
    """Return (unlocked: bool, reason: str)."""
    g = auth.gating()
    if session and session.get("pubkey") in g["admin_wallets"]:
        return True, "admin"
    if g.get("trading_unlocked"):
        return True, "released"
    mint = (g.get("token_mint") or "").strip()
    if mint and _token_bonded(mint):
        return True, "token_bonded"
    return False, "locked_until_bond"


def _jup(method, path, params=None, body=None):
    if _S is None:
        return {"_error": "requests_missing"}
    try:
        if method == "GET":
            r = _S.get(JUP + path, params=params, timeout=TIMEOUT)
        else:
            r = _S.post(JUP + path, json=body, timeout=TIMEOUT)
        if r.status_code != 200:
            try:
                msg = (r.json().get("error") or "")[:140]
            except Exception:
                msg = ""
            return {"_error": f"jupiter_{r.status_code}", "detail": msg}
        return r.json()
    except Exception as e:
        return {"_error": type(e).__name__}


def quote(input_mint, output_mint, amount_raw, slippage_bps=100):
    return _jup("GET", "/quote", params={
        "inputMint": input_mint, "outputMint": output_mint,
        "amount": int(amount_raw), "slippageBps": int(slippage_bps),
    })


def build_swap_tx(quote_resp, user_pubkey):
    return _jup("POST", "/swap", body={
        "quoteResponse": quote_resp,
        "userPublicKey": user_pubkey,
        "wrapAndUnwrapSol": True,
        "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": "auto",
    })


def prepare(session, side, mint, sol_amount=None, pct=None, slippage_bps=100,
            execute=False):
    """Quote (and optionally build) a swap for the session's wallet.

    side='buy'  : spend `sol_amount` SOL for `mint`
    side='sell' : sell `pct`% of the wallet's `mint` balance for SOL
    """
    ok, reason = unlocked(session)
    if not ok:
        return {"error": "locked", "reason": reason}
    pubkey = session.get("pubkey")
    if not pubkey:
        return {"error": "auth"}
    mint = (mint or "").strip()
    if not auth._B58_OK(mint):
        return {"error": "bad_mint"}
    try:
        slippage_bps = max(10, min(3000, int(slippage_bps)))
    except (TypeError, ValueError):
        slippage_bps = 100

    decimals = helius.token_decimals(mint)

    if side == "buy":
        try:
            sol = float(sol_amount)
        except (TypeError, ValueError):
            return {"error": "bad_amount"}
        if not (0.001 <= sol <= MAX_SOL_PER_TRADE):
            return {"error": "bad_amount",
                    "reason": f"Amount must be 0.001–{MAX_SOL_PER_TRADE} SOL."}
        q = quote(WSOL, mint, int(sol * 1e9), slippage_bps)
        if q.get("_error"):
            return {"error": q["_error"], "reason": q.get("detail") or
                    "No route — token may not be tradable on AMMs yet."}
        out_ui = (int(q.get("outAmount", 0)) / (10 ** decimals)) if decimals is not None else None
        summary = {"side": "buy", "in": f"{sol} SOL",
                   "out_ui": out_ui, "out_raw": q.get("outAmount"),
                   "price_impact_pct": q.get("priceImpactPct"),
                   "slippage_bps": slippage_bps}
    elif side == "sell":
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            return {"error": "bad_amount"}
        if not (1 <= pct <= 100):
            return {"error": "bad_amount", "reason": "Sell 1–100% of balance."}
        raw, dec2 = helius.token_balance_raw(pubkey, mint)
        if dec2 is not None:
            decimals = dec2
        if raw <= 0:
            return {"error": "no_balance", "reason": "This wallet holds none of that token."}
        amount = int(raw * pct / 100)
        if amount <= 0:
            return {"error": "bad_amount"}
        q = quote(mint, WSOL, amount, slippage_bps)
        if q.get("_error"):
            return {"error": q["_error"], "reason": q.get("detail") or "No route for this token."}
        in_ui = amount / (10 ** decimals) if decimals is not None else None
        summary = {"side": "sell", "in_ui": in_ui, "pct": pct,
                   "out_sol": int(q.get("outAmount", 0)) / 1e9,
                   "price_impact_pct": q.get("priceImpactPct"),
                   "slippage_bps": slippage_bps}
    else:
        return {"error": "bad_side"}

    result = {"ok": True, "summary": summary}
    if execute:
        tx = build_swap_tx(q, pubkey)
        if tx.get("_error"):
            return {"error": tx["_error"], "reason": tx.get("detail") or "Swap build failed."}
        result["tx"] = tx.get("swapTransaction")
        if not result["tx"]:
            return {"error": "no_tx", "reason": "Jupiter returned no transaction."}
    return result
