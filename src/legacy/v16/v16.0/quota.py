"""Quota, tiers and effort levels for the public widget (v12.0).

The widget is the only publicly reachable part of GroundedOps. Every
question costs real money (an LLM call) and real CPU (embedding,
reranking, grounding), so the public path needs a hard budget per caller.

TIERS
    anonymous  a visitor who has not signed in on the website
    member     signed in on the company website (verified by a signed
               token the website issues - see verify_token)
    staff      support department, issued a long-lived token

Members get double the anonymous allowance and are the only ones who may
request the higher-effort answer, because that is the expensive path.

CREDITS, NOT REQUESTS
Limits are counted in credits rather than questions, so a "give me a
better answer" retry can cost more than a standard question without
needing a second counter. Costs are configurable; defaults:

    standard   1 credit    fast model, normal retrieval
    deep       4 credits   stronger model, wider retrieval, reasoning role

IDENTITY AND EVASION
Anonymous callers are identified by a browser-generated visitor id, which
a determined user can clear. So anonymous usage is ALSO capped per client
IP at a higher ceiling: clearing storage gets you a fresh visitor budget
but never escapes the IP budget. Members are identified by the signed
token, which cannot be forged without the shared secret.

Storage is SQLite: one row per (identity, window). This is a single-site
deployment with modest volume, so a database is the right amount of
machinery - no Redis to run, and the counters survive a restart, which an
in-memory dict would not.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("QUOTA_DB_PATH", "quota.db")

# Secret shared with the company website, which uses it to sign tokens for
# signed-in users. MUST be set in production; an unset secret disables
# member tier entirely rather than defaulting to something guessable.
WIDGET_TOKEN_SECRET = os.getenv("WIDGET_TOKEN_SECRET", "")

# Rolling window length. A day is the natural unit for "you have used your
# questions for today" and is easy to explain in the UI.
WINDOW_SECONDS = int(os.getenv("QUOTA_WINDOW_SECONDS", str(24 * 60 * 60)))

# Credit allowances per window.
#
# Anonymous callers get ZERO credits by design: they cannot reach the
# generative pipeline at all, only the curated FAQ. That single decision
# removes the entire public cost exposure (no LLM call is reachable
# without an account) and the whole prompt-injection surface (untrusted
# input never reaches a prompt). Their limit below is only an
# abuse ceiling on FAQ lookups, which are pure local retrieval.
LIMITS = {
    "anonymous": 0,
    "member": int(os.getenv("QUOTA_MEMBER", "25")),
    "staff": int(os.getenv("QUOTA_STAFF", "500")),
}


# ── policy-backed limits ────────────────────────────────────────────────
# The values above are the env-derived FALLBACK. An operator can change any
# of them from the console (see policy.py), and a change has to take effect
# on the next request rather than at the next restart, so every read goes
# through these helpers instead of the constants directly.
#
# policy.py is imported lazily and every failure falls back to the constant:
# a broken or missing policy file must degrade to the deployment default, not
# take quota enforcement offline (which would fail open).
def _policy(key, fallback):
    try:
        import policy
        v = policy.value(key)
        return fallback if v is None else v
    except Exception:
        return fallback


def limit_for(tier: str) -> int:
    """Daily credit allowance. Anonymous is 0 unless the operator has
    deliberately enabled LLM access for signed-out visitors."""
    if tier == "anonymous":
        if not _policy("anon_llm_enabled", False):
            return 0
        return int(_policy("anon_llm_credits", 0))
    if tier == "member":
        return int(_policy("member_daily_credits", LIMITS["member"]))
    if tier == "staff":
        return int(_policy("staff_daily_credits", LIMITS["staff"]))
    return 0


def anon_faq_limit() -> int:
    return int(_policy("anon_faq_daily", ANON_FAQ_LIMIT))


def anon_ip_limit() -> int:
    return int(_policy("anon_ip_daily", ANON_IP_LIMIT))


def anon_llm_enabled() -> bool:
    return bool(_policy("anon_llm_enabled", False))

# FAQ lookups are cheap (no LLM, no external call), so anonymous visitors
# get a generous allowance - enough that a real person never hits it, low
# enough that nobody scrapes the whole FAQ in a loop.
ANON_FAQ_LIMIT = int(os.getenv("QUOTA_ANON_FAQ", "60"))

# Per-IP ceiling for anonymous traffic, independent of visitor id. Sized
# for a shared office NAT while still stopping a scripted drain.
ANON_IP_LIMIT = int(os.getenv("QUOTA_ANON_IP", "200"))

# Effort levels: credit cost and what they unlock. The MODEL IS CHOSEN
# SERVER-SIDE from this table - the client only sends a level name. Letting
# a browser name the model would let anyone select the most expensive one.
EFFORT = {
    # Curated FAQ lookup only - no LLM, no retrieval over documents.
    # The only level available without an account.
    "faq_only": {
        "credits": 0,
        "role": None,
        "top_k": 0,
        "min_tier": "anonymous",
    },
    "standard": {
        "credits": int(os.getenv("EFFORT_STANDARD_CREDITS", "1")),
        "role": "accurate",
        "top_k": 5,
        "min_tier": "member",
    },
    "deep": {
        "credits": int(os.getenv("EFFORT_DEEP_CREDITS", "4")),
        "role": "reasoning",
        "top_k": 10,
        "min_tier": "member",
        # Optional explicit override, e.g. ONLINE_DEEP_MODEL=deepseek-v4
        "model_env": "ONLINE_DEEP_MODEL",
    },
}

_TIER_RANK = {"anonymous": 0, "member": 1, "staff": 2}

_lock = threading.Lock()


# ── storage ───────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB_PATH, timeout=5)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS usage (
                identity     TEXT NOT NULL,
                window_start INTEGER NOT NULL,
                credits      INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (identity, window_start)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_window ON usage(window_start)")
    logger.info(f"Quota store ready at {DB_PATH}")


def _window_start(now: float | None = None) -> int:
    now = now or time.time()
    return int(now // WINDOW_SECONDS) * WINDOW_SECONDS


def _purge_old(c) -> None:
    """Drop windows older than two periods. Keeps the table small without a
    scheduled job - the cost is one DELETE on an indexed column."""
    c.execute("DELETE FROM usage WHERE window_start < ?",
              (_window_start() - 2 * WINDOW_SECONDS,))


# ── tokens ────────────────────────────────────────────────────────────

def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(user_id: str, tier: str = "member", ttl_seconds: int = 86400) -> str:
    """Mint a token. Included so the SUPPORT DEPARTMENT can generate staff
    tokens locally; the website mints member tokens itself using the same
    secret and algorithm (payload b64url + '.' + HMAC-SHA256 b64url)."""
    if not WIDGET_TOKEN_SECRET:
        raise RuntimeError("WIDGET_TOKEN_SECRET is not set")
    payload = {"uid": user_id, "tier": tier, "exp": int(time.time()) + ttl_seconds}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    sig = hmac.new(WIDGET_TOKEN_SECRET.encode(), raw, hashlib.sha256).digest()
    return raw.decode() + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()


def verify_token(token: str | None) -> dict | None:
    """Validate a website-issued token. Returns {uid, tier} or None.

    Any failure returns None and the caller is treated as anonymous - a bad
    token must never be an error the visitor sees, and must never upgrade
    anyone. Signature comparison is constant-time.
    """
    if not token or not WIDGET_TOKEN_SECRET:
        return None
    try:
        raw_s, sig_s = token.split(".", 1)
        expected = hmac.new(WIDGET_TOKEN_SECRET.encode(), raw_s.encode(),
                            hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64d(sig_s)):
            logger.warning("widget token: bad signature")
            return None
        payload = json.loads(_b64d(raw_s))
        if int(payload.get("exp", 0)) < time.time():
            return None
        tier = payload.get("tier", "member")
        if tier not in _TIER_RANK:
            return None
        return {"uid": str(payload.get("uid", "")), "tier": tier}
    except Exception as e:
        logger.warning(f"widget token: rejected ({e})")
        return None


# ── identity ──────────────────────────────────────────────────────────

def identify(token: str | None, visitor_id: str | None, client_ip: str) -> dict:
    """Resolve a caller to {tier, identity, ip_identity}.

    Identities are hashed so the quota database holds no raw IP addresses
    or visitor ids - it only ever needs to compare them, never read them
    back, and that keeps this table out of scope for a privacy review.
    """
    claims = verify_token(token)
    if claims:
        return {
            "tier": claims["tier"],
            "identity": "u:" + _h(claims["uid"]),
            "ip_identity": None,          # members are not IP-capped
            "uid": claims["uid"],
        }
    vid = (visitor_id or "").strip() or "none"
    return {
        "tier": "anonymous",
        "identity": "v:" + _h(f"{vid}|{client_ip}"),
        "ip_identity": "i:" + _h(client_ip),
        "uid": None,
    }


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:32]


# ── effort ────────────────────────────────────────────────────────────

def resolve_effort(requested: str | None, tier: str) -> tuple[str, dict]:
    """Pick the effort level actually allowed for this tier.

    A request for "deep" from an anonymous caller silently downgrades to
    standard rather than erroring: the widget shows the upsell, and the
    visitor still gets an answer.
    """
    # An anonymous visitor normally tops out at faq_only. When the operator
    # has deliberately opened AI to signed-out visitors, they are treated as
    # a member for the purpose of choosing an effort level -- their much
    # smaller credit allowance is what bounds them, not the tier gate.
    effective = tier
    if tier == "anonymous" and anon_llm_enabled():
        effective = "member"

    default = "faq_only" if effective == "anonymous" else "standard"
    level = (requested or default).strip().lower()
    spec = EFFORT.get(level)
    if not spec:
        return default, EFFORT[default]
    if _TIER_RANK[effective] < _TIER_RANK[spec["min_tier"]]:
        # Anonymous asking for a generated answer falls back to FAQ-only;
        # a member asking for "deep" falls back to standard. Both get an
        # answer plus an upsell rather than an error.
        return default, EFFORT[default]
    return level, spec


# ── the check ─────────────────────────────────────────────────────────

def check_faq_lookup(caller: dict) -> dict:
    """Abuse ceiling for anonymous FAQ retrieval. Members are not counted
    here - their credit budget already bounds them."""
    if caller["tier"] != "anonymous":
        return {"allowed": True, "remaining": None, "limit": None,
                "reset_at": _window_start() + WINDOW_SECONDS, "reason": None}
    win = _window_start()
    key = caller["identity"] + ":faq"
    with _lock, _conn() as c:
        used = _used(c, key, win)
    faq_cap = anon_faq_limit()
    if used >= faq_cap:
        return {"allowed": False, "remaining": 0, "limit": faq_cap,
                "reset_at": win + WINDOW_SECONDS, "reason": "faq_quota"}
    return {"allowed": True, "remaining": faq_cap - used,
            "limit": faq_cap, "reset_at": win + WINDOW_SECONDS,
            "reason": None}


def consume_faq_lookup(caller: dict) -> None:
    if caller["tier"] != "anonymous":
        return
    win = _window_start()
    with _lock, _conn() as c:
        _add(c, caller["identity"] + ":faq", win, 1)
        if caller.get("ip_identity"):
            _add(c, caller["ip_identity"] + ":faq", win, 1)


def check(caller: dict, cost: int) -> dict:
    """Would this request fit in the caller's budget? Does NOT consume.

    Returns {allowed, remaining, limit, reset_at, reason}.
    """
    limit = limit_for(caller["tier"])
    win = _window_start()
    with _lock, _conn() as c:
        used = _used(c, caller["identity"], win)
        remaining = max(0, limit - used)
        if used + cost > limit:
            return {"allowed": False, "remaining": remaining, "limit": limit,
                    "reset_at": win + WINDOW_SECONDS, "reason": "quota"}
        # Anonymous callers are additionally capped per IP so clearing
        # browser storage cannot mint a fresh allowance.
        if caller.get("ip_identity"):
            ip_used = _used(c, caller["ip_identity"], win)
            if ip_used + cost > anon_ip_limit():
                return {"allowed": False, "remaining": 0, "limit": limit,
                        "reset_at": win + WINDOW_SECONDS, "reason": "ip_quota"}
    return {"allowed": True, "remaining": remaining, "limit": limit,
            "reset_at": win + WINDOW_SECONDS, "reason": None}


def consume(caller: dict, cost: int) -> dict:
    """Deduct credits. Call AFTER a successful answer, so a failed or
    refused request doesn't burn the visitor's allowance - the common
    complaint about naive rate limiting."""
    win = _window_start()
    with _lock, _conn() as c:
        _add(c, caller["identity"], win, cost)
        if caller.get("ip_identity"):
            _add(c, caller["ip_identity"], win, cost)
        _purge_old(c)
        used = _used(c, caller["identity"], win)
    limit = limit_for(caller["tier"])
    return {"remaining": max(0, limit - used), "limit": limit,
            "reset_at": win + WINDOW_SECONDS}


def status(caller: dict) -> dict:
    """What the widget's settings panel shows the user."""
    tier = caller["tier"]
    win = _window_start()
    with _lock, _conn() as c:
        used = _used(c, caller["identity"], win)
        faq_used = _used(c, caller["identity"] + ":faq", win)

    if tier == "anonymous":
        # Two shapes of anonymous, decided by the operator in the console.
        # Default: curated FAQ only, and the widget says why. With AI opened
        # up: a small credit allowance, reported in the same units a member
        # sees so the widget needs no special case.
        if anon_llm_enabled():
            ai_limit = limit_for("anonymous")
            return {
                "tier": "anonymous",
                "mode": "full",
                "limit": ai_limit,
                "used": used,
                "remaining": max(0, ai_limit - used),
                "unit": "credits",
                "reset_at": win + WINDOW_SECONDS,
                "ai_available": True,
                "deep_available": False,
                "standard_cost": EFFORT["standard"]["credits"],
                "deep_cost": EFFORT["deep"]["credits"],
                "account_notice": "",
            }
        faq_cap = anon_faq_limit()
        return {
            "tier": "anonymous",
            "mode": "faq_only",
            "limit": faq_cap,
            "used": faq_used,
            "remaining": max(0, faq_cap - faq_used),
            "unit": "FAQ lookups",
            "reset_at": win + WINDOW_SECONDS,
            "ai_available": False,
            "deep_available": False,
            "deep_cost": EFFORT["deep"]["credits"],
            # Shown by the widget instead of it inventing its own wording.
            "account_notice": _policy("anon_notice", ""),
        }

    limit = limit_for(tier)
    return {
        "tier": tier,
        "mode": "full",
        "limit": limit,
        "used": used,
        "remaining": max(0, limit - used),
        "unit": "credits",
        "reset_at": win + WINDOW_SECONDS,
        "ai_available": True,
        "deep_available": True,
        "standard_cost": EFFORT["standard"]["credits"],
        "deep_cost": EFFORT["deep"]["credits"],
    }


def _used(c, identity: str, win: int) -> int:
    row = c.execute("SELECT credits FROM usage WHERE identity=? AND window_start=?",
                    (identity, win)).fetchone()
    return row[0] if row else 0


def _add(c, identity: str, win: int, cost: int) -> None:
    c.execute("""
        INSERT INTO usage (identity, window_start, credits) VALUES (?, ?, ?)
        ON CONFLICT(identity, window_start) DO UPDATE SET credits = credits + ?
    """, (identity, win, cost, cost))


# ── per-session caps ───────────────────────────────────────────────────
# Distinct from the daily allowance. The daily figure stops someone using a
# month of budget in a day; these stop one runaway conversation using a
# whole day's budget in ten minutes. Both are wanted: a visitor who leaves a
# tab open with a script in it hits these long before the daily one.
#
# Counted in the same usage table, under a session-scoped identity, so there
# is one place that knows how to count and one place that gets purged. A
# session id is client-supplied, so these are a cost guard and NOT a security
# boundary — a caller who wants a fresh session can ask for one, and the
# daily per-visitor and per-IP ceilings are what actually bound that.

def _session_key(session_id: str, what: str) -> str:
    return f"sess:{session_id}:{what}"


def session_check(session_id: str | None, tokens_wanted: int = 0) -> dict:
    """Would one more question (and optionally `tokens_wanted` tokens) fit in
    this session's caps? A cap of 0 means unlimited, so the common
    configuration costs one policy read and no database work."""
    q_cap = int(_policy("questions_per_session", 0))
    t_cap = int(_policy("tokens_per_session", 0))
    if not session_id or (q_cap <= 0 and t_cap <= 0):
        return {"allowed": True, "reason": None,
                "questions_limit": q_cap, "questions_used": 0,
                "tokens_limit": t_cap, "tokens_used": 0}

    win = _window_start()
    with _lock, _conn() as c:
        q_used = _used(c, _session_key(session_id, "q"), win)
        t_used = _used(c, _session_key(session_id, "t"), win)

    if q_cap > 0 and q_used >= q_cap:
        return {"allowed": False, "reason": "session_questions",
                "questions_limit": q_cap, "questions_used": q_used,
                "tokens_limit": t_cap, "tokens_used": t_used}
    if t_cap > 0 and t_used + max(0, tokens_wanted) > t_cap:
        return {"allowed": False, "reason": "session_tokens",
                "questions_limit": q_cap, "questions_used": q_used,
                "tokens_limit": t_cap, "tokens_used": t_used}
    return {"allowed": True, "reason": None,
            "questions_limit": q_cap, "questions_used": q_used,
            "tokens_limit": t_cap, "tokens_used": t_used}


def session_record(session_id: str | None, tokens_used: int = 0) -> None:
    """Count one question, and whatever tokens it cost, against the session.

    Recorded AFTER the answer, because the token cost is not known before it
    and charging an estimate would either overcharge every short answer or
    let a long one through free.
    """
    if not session_id:
        return
    q_cap = int(_policy("questions_per_session", 0))
    t_cap = int(_policy("tokens_per_session", 0))
    if q_cap <= 0 and t_cap <= 0:
        return
    win = _window_start()
    with _lock, _conn() as c:
        _add(c, _session_key(session_id, "q"), win, 1)
        if tokens_used > 0:
            _add(c, _session_key(session_id, "t"), win, int(tokens_used))
