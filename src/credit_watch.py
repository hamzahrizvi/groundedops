"""Warn the root admins by email before a provider runs out of credit.

WHAT CAN BE CHECKED

A provider only counts if it will say how much is left:

  deepseek   GET /user/balance -- the account's remaining balance.
  openai     Only when the slot points at a LiteLLM gateway (the on-prem
             one does): GET /key/info reports the key's spend against its
             max_budget. A key with no budget has nothing to run out of, and
             OpenAI's own API publishes no balance at all.
  anthropic  No balance API.

Anything else reports "unsupported" rather than guessing. The console shows
that, so a silent slot is never mistaken for a healthy one.

TWO TRIGGERS

  * a periodic check (CREDIT_CHECK_HOURS, default 6; 0 turns it off), and
  * report_exhausted(), called by llm.py the moment a provider answers
    402 / "budget exceeded" -- the case the periodic check exists to get
    ahead of, but which must still reach someone if it didn't.

One email per provider per REMIND_HOURS while it stays low, and the state
re-arms as soon as a check sees it recover -- so a top-up followed by a
second drain alerts again instead of being swallowed by the first alert.

The key is only ever sent to the provider it belongs to, on the same base
URL answering already uses. Nothing here logs or returns a key.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone

import requests

import accounts
import jsonstore
import keystore
import mailer

logger = logging.getLogger(__name__)

DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"
REMIND_HOURS = 24
_STATE_PATH = os.getenv("CREDIT_ALERT_STATE_PATH", "credit_alerts.json")
_lock = threading.Lock()
# Serialises read-decide-send-write, so the periodic check and a 402 from the
# answer path landing together cannot both email about the same provider.
_alert_lock = threading.Lock()
_last_results: list[dict] = []
_last_checked: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── checks ──────────────────────────────────────────────────────────────

def _result(provider: str, status: str, **kw) -> dict:
    out = {"provider": provider, "label": keystore.label_for(provider),
           "kind": keystore.kind_label(provider), "status": status,
           "balance": None, "currency": None, "detail": ""}
    out.update(kw)
    return out


def _check_deepseek(threshold: float) -> dict:
    key = keystore.get_key("deepseek")
    res = requests.get(DEEPSEEK_BALANCE_URL, timeout=10,
                       headers={"Authorization": f"Bearer {key}",
                                "Accept": "application/json"})
    res.raise_for_status()
    body = res.json()
    infos = body.get("balance_infos") or []
    if not infos:
        return _result("deepseek", "unknown", detail="no balance reported")
    # One entry per currency. Judge by the largest, so a USD account with a
    # stray zero-CNY row is not reported as empty.
    best = max(infos, key=lambda i: float(i.get("total_balance") or 0))
    bal = float(best.get("total_balance") or 0)
    cur = best.get("currency") or ""
    if body.get("is_available") is False or bal <= 0:
        status = "empty"
    elif bal < threshold:
        status = "low"
    else:
        status = "ok"
    return _result("deepseek", status, balance=round(bal, 2), currency=cur)


def _gateway_root() -> str:
    from llm import OPENAI_BASE
    base = OPENAI_BASE.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _check_openai_slot(threshold: float) -> dict:
    root = _gateway_root()
    if "api.openai.com" in root:
        return _result("openai", "unsupported",
                       detail="OpenAI does not publish a balance API")
    key = keystore.get_key("openai")
    res = requests.get(f"{root}/key/info", timeout=10,
                       headers={"Authorization": f"Bearer {key}"})
    if res.status_code in (404, 405):
        return _result("openai", "unsupported",
                       detail="this endpoint does not report a budget")
    res.raise_for_status()
    info = (res.json() or {}).get("info") or {}
    budget, spend = info.get("max_budget"), float(info.get("spend") or 0)
    if budget is None:
        return _result("openai", "unlimited",
                       detail=f"no budget set on this key (spent {spend:.2f})")
    left = float(budget) - spend
    status = "empty" if left <= 0 else "low" if left < threshold else "ok"
    return _result("openai", status, balance=round(left, 2), currency="USD",
                   detail=f"{spend:.2f} of {float(budget):.2f} budget used")


_CHECKS = {"deepseek": _check_deepseek, "openai": _check_openai_slot}


def check_all() -> list[dict]:
    """One result per provider that has a key. Never raises."""
    global _last_results, _last_checked
    threshold = keystore.get_credit_threshold()
    out = []
    for p in keystore.providers():
        if not keystore.has_key(p):
            continue
        fn = _CHECKS.get(p)
        if not fn:
            out.append(_result(p, "unsupported",
                               detail="this provider has no balance API"))
            continue
        try:
            out.append(fn(threshold))
        except Exception as e:
            # Type and HTTP status only: a requests error stringifies to the
            # full URL, and the full text is in the log for whoever needs it.
            code = getattr(getattr(e, "response", None), "status_code", None)
            logger.info(f"credit check failed for {p}: {e}")
            out.append(_result(p, "error", detail=type(e).__name__
                               + (f" (HTTP {code})" if code else "")))
    with _lock:
        _last_results, _last_checked = out, _now_iso()
    return out


def last_results() -> tuple[list[dict], str]:
    with _lock:
        return list(_last_results), _last_checked


# ── alerting ────────────────────────────────────────────────────────────

def recipients() -> list[str]:
    """CREDIT_ALERT_EMAIL when set (comma-separated), otherwise every active
    root account -- the people who can actually add a key or top one up."""
    override = (os.getenv("CREDIT_ALERT_EMAIL") or "").strip()
    if override:
        return [a.strip() for a in override.split(",") if a.strip()]
    return [u["email"] for u in accounts.list_users()
            if u["level"] == "root" and not u["disabled"]]


def _describe(r: dict, threshold: float) -> str:
    if r["status"] == "exhausted":
        return (f"{r['label']} ({r['kind']}) refused a request for lack of "
                f"credit. Answers routed to it are failing over or failing "
                f"until it is topped up.")
    amount = (f"{r['balance']:.2f} {r['currency'] or ''}".strip()
              if r.get("balance") is not None else "unknown")
    word = "has run out" if r["status"] == "empty" else "is running low"
    return (f"{r['label']} ({r['kind']}) {word}: {amount} left, below the "
            f"alert threshold of {threshold:g}."
            + (f" {r['detail']}." if r.get("detail") else ""))


def _send_alert(alerts: list[dict]) -> bool:
    to = recipients()
    if not to:
        logger.warning("low-credit alert: no active root account to email")
        return False
    if not mailer.is_configured():
        logger.warning("low-credit alert not sent: SMTP is not configured "
                       "(API keys page > Low-credit alerts)")
        return False
    threshold = keystore.get_credit_threshold()
    names = ", ".join(a["label"] for a in alerts)
    body = ("GroundedOps low-credit alert\n\n"
            + "\n\n".join(_describe(a, threshold) for a in alerts)
            + "\n\nTop up the account, or move its jobs to another key on the "
              "console's API keys page. You will be reminded every "
              f"{REMIND_HOURS} hours while it stays low.\n")
    try:
        mailer.send(to, f"[GroundedOps] Credit low: {names}", body)
        return True
    except mailer.MailError as e:
        logger.warning(f"low-credit alert not sent: {e}")
        return False


def maybe_alert(results: list[dict]) -> list[str]:
    """Email about any provider that is low and not alerted recently.
    Returns the providers alerted. Recovered providers are re-armed."""
    with _alert_lock:
        return _maybe_alert(results)


def _maybe_alert(results: list[dict]) -> list[str]:
    state = jsonstore.load(_STATE_PATH, {}, label="credit alerts")
    if not isinstance(state, dict):
        state = {}
    now = time.time()
    due, changed = [], False
    for r in results:
        p = r["provider"]
        if r["status"] == "ok":
            if p in state:
                state.pop(p)
                changed = True
            continue
        if r["status"] not in ("low", "empty", "exhausted"):
            continue
        if now - float(state.get(p, 0)) >= REMIND_HOURS * 3600:
            due.append(r)
    if due and _send_alert(due):
        for r in due:
            state[r["provider"]] = now
        changed = True
    if changed:
        try:
            jsonstore.save(_STATE_PATH, state, label="credit alerts")
        except Exception as e:
            logger.warning(f"could not save credit alert state: {e}")
    return [r["provider"] for r in due] if due else []


def report_exhausted(provider: str) -> None:
    """Called from the answer path on a 402 / budget-exceeded response.
    Returns immediately; the email goes out on a thread."""
    def run():
        try:
            maybe_alert([_result(provider, "exhausted")])
        except Exception as e:
            logger.warning(f"exhausted-credit alert failed: {e}")
    threading.Thread(target=run, daemon=True).start()


# ── periodic check ──────────────────────────────────────────────────────

def _interval_seconds() -> float:
    try:
        return float(os.getenv("CREDIT_CHECK_HOURS", "6")) * 3600
    except ValueError:
        return 6 * 3600


def _loop() -> None:
    time.sleep(60)   # let startup and warmup finish first
    while True:
        interval = _interval_seconds()
        if interval <= 0:
            return
        try:
            maybe_alert(check_all())
        except Exception as e:
            logger.warning(f"periodic credit check failed: {e}")
        time.sleep(interval)


def start() -> None:
    if _interval_seconds() <= 0:
        logger.info("periodic credit check disabled (CREDIT_CHECK_HOURS=0)")
        return
    threading.Thread(target=_loop, name="credit-watch", daemon=True).start()
