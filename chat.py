"""
Community chatroom backend for Sentinels.

- Wallet-authenticated posting (the server passes the verified pubkey).
- Moderation in two layers:
    1. a fast banned-keyword / leetspeak filter (always on), and
    2. optional Claude AI moderation (activated when `anthropic_api_key` is set in
       config.json or the ANTHROPIC_API_KEY env var) to catch obfuscated slurs,
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
MOD_MODEL = os.environ.get("SM_MOD_MODEL", "claude-haiku-4-5-20251001")

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


def _anthropic_key():
    k = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if k:
        return k
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            return (json.load(f).get("anthropic_api_key") or "").strip()
    except Exception:
        return ""


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
    """Return (block: bool, reason). No-op (False) when no key/library/usable response."""
    key = _anthropic_key()
    if not key or _S is None:
        return False, None
    try:
        r = _S.post("https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": MOD_MODEL, "max_tokens": 60,
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
    return bool(_anthropic_key()) and _S is not None
