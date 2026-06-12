"""
Community chatroom backend for Sentinels.

- Wallet-authenticated posting (the server passes the verified pubkey).
- Moderation in two layers:
    1. a fast banned-keyword / leetspeak filter (always on), and
    2. an optional AI moderation layer (activated by the `moderation` block in
       config.json — a configurable LLM endpoint) to catch obfuscated slurs,
       harassment, scam-shilling and coordinated FUD that keyword lists miss.
- Per-wallet rate limiting + a rolling in-memory log persisted to chat_log.json.

Reads are public; posting requires a wallet session (enforced in server.py).
"""

import os
import re
import json
import time
import threading

try:
    import requests
    _S = requests.Session()
except Exception:                       # pragma: no cover
    requests = None
    _S = None

HERE = os.path.dirname(os.path.abspath(__file__))
CHAT_FILE = os.path.join(HERE, "chat_log.json")
CONFIG = os.path.join(HERE, "config.json")

MAX_KEEP = 300
MAX_LEN = 300
RATE_SECONDS = 3

# Banned keywords (whole-word, case- & leetspeak-insensitive). Includes the explicit
# project blocklist plus common slurs/abuse. The AI layer catches the rest.
BANNED = {
    "larp", "scam", "scammer", "scamming", "rug", "rugpull", "rugged", "ponzi",
    "honeypot", "jeet", "fud", "fudder", "fudding", "exit scam", "dump it",
    "gay", "fag", "faggot", "retard", "retarded", "nigger", "nigga", "kike",
    "tranny", "slut", "whore", "rape",
}
_LEET = str.maketrans("4310$5!", "aeiossi")

# ---------------------------------------------------------------------------
_lock = threading.Lock()
_messages = []
_next_id = [1]
_last_post = {}


def _load():
    try:
        with open(CHAT_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        _messages.extend(d.get("messages", []))
        _next_id[0] = d.get("next_id", len(_messages) + 1)
    except Exception:
        pass


def _save():
    try:
        with open(CHAT_FILE, "w", encoding="utf-8") as f:
            json.dump({"messages": _messages[-MAX_KEEP:], "next_id": _next_id[0]}, f)
    except Exception:
        pass


_load()


def _mod_cfg():
    """Optional AI moderation provider, fully config-driven (no provider hardcoded).
    Reads the `moderation` block from config.json: {api_key, url, model, headers}.
    Returns {} when not configured -> AI layer stays off (keyword filter still runs)."""
    cfg = {}
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f).get("moderation") or {}
    except Exception:
        cfg = {}
    key = os.environ.get("SM_MOD_KEY", "").strip() or (cfg.get("api_key") or "").strip()
    url = (cfg.get("url") or "").strip()
    model = (cfg.get("model") or "").strip()
    if not (key and url and model) or _S is None:
        return {}
    return {"key": key, "url": url, "model": model, "headers": cfg.get("headers") or {}}


def _despace_singletons(text):
    """Collapse runs of single characters ('f u d' -> 'fud') to defeat spacing evasion."""
    out, run = [], []
    for tk in text.split():
        if len(tk) == 1 and tk.isalnum():
            run.append(tk)
        else:
            if run:
                out.append("".join(run)); run = []
            out.append(tk)
    if run:
        out.append("".join(run))
    return " ".join(out)


def keyword_hit(text):
    base = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    variants = {base, base.translate(_LEET),
                _despace_singletons(base), _despace_singletons(base).translate(_LEET)}
    for v in variants:
        s = " " + v + " "
        for w in BANNED:
            # word-start boundary + stem match (catches plurals/suffixes: scam->scammers)
            pat = r"(?<![a-z])" + re.escape(w).replace(r"\ ", r"\s+")
            if re.search(pat, s):
                return w
    return None


_MOD_SYSTEM = (
    "You moderate the community chat of 'Sentinels', a Solana on-chain analytics project. "
    "Decide if a single chat message should be BLOCKED. Block if it contains: hate speech, "
    "slurs or offensive language; harassment or personal attacks; spam, scam promotion, or "
    "phishing/malicious links; or coordinated FUD whose clear intent is to damage the project's "
    "reputation with baseless claims (e.g. calling it a scam/rug/larp with no substance). "
    "Allow normal chat, genuine questions, hype, and civil substantive feedback. "
    "Respond with ONLY a compact JSON object: {\"block\": true|false, \"reason\": \"<=8 words\"}."
)


def ai_moderate(text):
    """Return (block: bool, reason). No-op (False) when the provider isn't configured."""
    c = _mod_cfg()
    if not c:
        return False, None
    try:
        headers = {"x-api-key": c["key"], "content-type": "application/json"}
        headers.update(c["headers"])            # any provider-specific headers live in config
        r = _S.post(c["url"], headers=headers,
                    json={"model": c["model"], "max_tokens": 60,
                          "system": _MOD_SYSTEM,
                          "messages": [{"role": "user", "content": text[:500]}]},
                    timeout=8)
        if r.status_code != 200:
            return False, None
        txt = "".join(b.get("text", "") for b in r.json().get("content", []))
        m = re.search(r"\{.*\}", txt, re.S)
        data = json.loads(m.group(0) if m else txt)
        return bool(data.get("block")), data.get("reason")
    except Exception:
        return False, None


def post(pubkey, alias, text):
    text = (text or "").strip()
    if not text:
        return {"error": "empty"}
    if len(text) > MAX_LEN:
        return {"error": "too_long", "reason": f"Keep it under {MAX_LEN} characters."}
    now = time.time()
    with _lock:
        if now - _last_post.get(pubkey, 0) < RATE_SECONDS:
            return {"error": "rate", "reason": "Slow down a moment."}

    hit = keyword_hit(text)
    if hit:
        return {"error": "blocked", "reason": "Blocked: that's not allowed here."}
    block, reason = ai_moderate(text)
    if block:
        return {"error": "blocked", "reason": reason or "Blocked by moderation."}

    with _lock:
        msg = {"id": _next_id[0], "pubkey": pubkey, "alias": alias,
               "text": text, "ts": int(now)}
        _next_id[0] += 1
        _messages.append(msg)
        if len(_messages) > MAX_KEEP:
            del _messages[:-MAX_KEEP]
        _last_post[pubkey] = now
        _save()
    return {"ok": True, "message": msg}


def recent(after=0, limit=80):
    with _lock:
        return [m for m in _messages if m["id"] > after][-limit:]


def ai_enabled():
    return bool(_mod_cfg())
