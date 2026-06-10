"""
Wallet auth + token-gating for the Solana Smart Money tracker.

Flow (Sign-In-With-Solana style, no transaction / no cost to the user):
    1. GET  /api/auth/nonce          -> server issues a one-time nonce + a message to sign
    2. wallet signs that message     (frontend, via the Wallet Standard)
    3. POST /api/auth/verify         -> server verifies the ed25519 signature proves the user
                                        controls the pubkey, checks on-chain gate-token balance,
                                        and issues an HMAC-signed session cookie.

Gating (server-side, so addresses never leave the server for non-holders):
    Wallets whose win rate >= `lock_winrate` are redacted for anyone who is not "premium".
    Premium = pubkey in admin_wallets, OR holds >= `min_usd` of `token_mint`.
    Until you launch `token_mint` (left blank), the app runs in PREVIEW mode: free users see the
    locked experience; only admin_wallets can preview the unlocked view.

Crypto: uses `cryptography` (fast) when present, else a self-contained pure-python ed25519 verify.
Only the standard library is required otherwise.
"""

import os
import json
import time
import hmac
import base64
import hashlib
import secrets
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
SECRET_FILE = os.path.join(HERE, ".session_secret")

NONCE_TTL = 300          # seconds a login nonce stays valid
SESSION_TTL = 86400      # seconds a session lasts (24h)


# ---------------------------------------------------------------------------
# base58 (Bitcoin alphabet) decode  — for Solana pubkeys / signatures
# ---------------------------------------------------------------------------
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_MAP = {c: i for i, c in enumerate(_B58)}


def b58decode(s):
    num = 0
    for ch in s:
        if ch not in _B58_MAP:
            raise ValueError("bad base58")
        num = num * 58 + _B58_MAP[ch]
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + body


# ---------------------------------------------------------------------------
# ed25519 verify
# ---------------------------------------------------------------------------
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    _HAS_CRYPTOGRAPHY = True
except Exception:
    _HAS_CRYPTOGRAPHY = False


def _verify_cryptography(pub, msg, sig):
    try:
        Ed25519PublicKey.from_public_bytes(pub).verify(sig, msg)
        return True
    except Exception:
        return False


# --- pure-python fallback (RFC 8032 reference, verify only) ---
_q = 2 ** 255 - 19
_d = None
_I = None
_B = None


def _expmod(b, e, m):
    r = 1
    b %= m
    while e:
        if e & 1:
            r = (r * b) % m
        e >>= 1
        b = (b * b) % m
    return r


def _inv(x):
    return _expmod(x, _q - 2, _q)


def _init_curve():
    global _d, _I, _B
    _d = (-121665 * _inv(121666)) % _q
    _I = _expmod(2, (_q - 1) // 4, _q)

    def xrec(y):
        xx = (y * y - 1) * _inv(_d * y * y + 1)
        x = _expmod(xx, (_q + 3) // 8, _q)
        if (x * x - xx) % _q != 0:
            x = (x * _I) % _q
        if x % 2 != 0:
            x = _q - x
        return x
    by = (4 * _inv(5)) % _q
    _B = [xrec(by) % _q, by % _q, xrec]


def _edwards(P, Q):
    x1, y1 = P[0], P[1]
    x2, y2 = Q[0], Q[1]
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + _d * x1 * x2 * y1 * y2)
    y3 = (y1 * y2 + x1 * x2) * _inv(1 - _d * x1 * x2 * y1 * y2)
    return [x3 % _q, y3 % _q]


def _scalarmult(P, e):
    Q = [0, 1]
    while e:
        if e & 1:
            Q = _edwards(Q, P)
        P = _edwards(P, P)
        e >>= 1
    return Q


def _bit(h, i):
    return (h[i // 8] >> (i % 8)) & 1


def _verify_pure(pub, msg, sig):
    if len(sig) != 64 or len(pub) != 32:
        return False
    if _d is None:
        _init_curve()
    xrec = _B[2]

    def decodepoint(s):
        y = sum(2 ** i * _bit(s, i) for i in range(255))
        x = xrec(y)
        if x & 1 != _bit(s, 255):
            x = _q - x
        return [x, y]
    try:
        A = decodepoint(pub)
        R = decodepoint(sig[:32])
        S = sum(2 ** i * _bit(sig[32:], i) for i in range(256))
        h = int.from_bytes(hashlib.sha512(sig[:32] + pub + msg).digest(), "little")
        return _scalarmult([_B[0], _B[1]], S) == _edwards(R, _scalarmult(A, h % (2 ** 252 + 27742317777372353535851937790883648493)))
    except Exception:
        return False


def verify_signature(pubkey_b58, message_str, signature_b64):
    """True iff `signature_b64` is a valid ed25519 sig of `message_str` by `pubkey_b58`."""
    try:
        pub = b58decode(pubkey_b58)
        sig = base64.b64decode(signature_b64)
        msg = message_str.encode("utf-8")
    except Exception:
        return False
    if len(pub) != 32:
        return False
    if _HAS_CRYPTOGRAPHY:
        return _verify_cryptography(pub, msg, sig)
    return _verify_pure(pub, msg, sig)


# ---------------------------------------------------------------------------
# Gating config
# ---------------------------------------------------------------------------
_DEFAULT_GATING = {
    "enabled": True,
    "lock_winrate": 50,     # wallets with winrate >= this are gated for free users
    "token_mint": "",       # your SPL token mint (blank => preview mode)
    "min_usd": 5,           # hold >= this USD of token_mint to unlock
    "admin_wallets": [],    # always-premium pubkeys (e.g. you)
    "pumpfun_mcap_target": 100000,   # project mcap that auto-releases the Pump.fun radar
}


def gating():
    g = dict(_DEFAULT_GATING)
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in (cfg.get("gating") or {}).items():
            g[k] = v
    except Exception:
        pass
    g["admin_wallets"] = set(g.get("admin_wallets") or [])
    return g


# ---------------------------------------------------------------------------
# Nonce store
# ---------------------------------------------------------------------------
_nonces = {}             # nonce -> (message, exp)
_nonce_lock = threading.Lock()


def issue_nonce():
    nonce = secrets.token_hex(16)
    msg = ("Solana Smart Money — sign in\n\n"
           f"Nonce: {nonce}\n"
           f"Issued: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n\n"
           "Signing is free and authorizes NO transaction. It only proves you control this wallet.")
    with _nonce_lock:
        now = time.time()
        for n in [n for n, (_, e) in _nonces.items() if e < now]:
            _nonces.pop(n, None)
        _nonces[nonce] = (msg, now + NONCE_TTL)
    return nonce, msg


def consume_nonce(nonce):
    with _nonce_lock:
        item = _nonces.pop(nonce, None)
    if not item:
        return None
    msg, exp = item
    return msg if exp >= time.time() else None


# ---------------------------------------------------------------------------
# HMAC session tokens (stateless, signed cookie)
# ---------------------------------------------------------------------------
def _secret():
    k = os.environ.get("SM_SESSION_SECRET", "").encode()
    if k:
        return k
    if os.path.isfile(SECRET_FILE):
        with open(SECRET_FILE, "rb") as f:
            return f.read().strip()
    k = secrets.token_hex(32).encode()
    try:
        with open(SECRET_FILE, "wb") as f:
            f.write(k)
    except Exception:
        pass
    return k


def _b64u(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64u_dec(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_session(data):
    body = _b64u(json.dumps(data, separators=(",", ":")).encode())
    sig = _b64u(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_session(token):
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expect = _b64u(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        data = json.loads(_b64u_dec(body))
    except Exception:
        return None
    if data.get("exp", 0) < time.time():
        return None
    return data


# ---------------------------------------------------------------------------
# Premium decision
# ---------------------------------------------------------------------------
def decide_premium(pubkey, balance_usd):
    """Return (premium: bool, reason: str)."""
    g = gating()
    if pubkey in g["admin_wallets"]:
        return True, "admin"
    if not g["enabled"]:
        return True, "gating_off"
    if not g["token_mint"]:
        return False, "preview"          # token not launched yet
    if balance_usd >= g["min_usd"]:
        return True, "holder"
    return False, "insufficient"
