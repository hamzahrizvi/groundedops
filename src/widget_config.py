"""Widget configuration and lead capture (v13.0).

Backs the console's "Widget design" page: the branding, the buttons a
visitor sees on the first message, and the two contact forms ("Talk to
sales", "Talk to support") those buttons can open.

TWO AUDIENCES, TWO ENDPOINTS
----------------------------
The config is read by the PUBLIC widget (unauthenticated — it is embedded
on a customer-facing page) and written by the ADMIN console. main.py
exposes those as two separate routes over this one module:

    GET  /widget/config   public, no auth  -> public_config()
    PUT  /admin/widget/config   admin only -> save()

public_config() is not just save()'s inverse: it strips anything the
widget has no business receiving. Right now that is the notification
email on each form — a config blob served to every visitor's browser is
not a place to put an internal address, and a leaked support inbox is a
spam target. Keep that asymmetry in mind when adding fields: default to
admin-only and promote deliberately.

VALIDATION
----------
Everything here is admin-authored and ends up rendered in a third-party
page, so input is normalized rather than trusted: field types are checked
against a whitelist, intro actions against another, labels are length-
capped, and the accent color must match a hex pattern. This is not
sanitizing for XSS (the widget must still escape on render — validation
here does not excuse that); it is keeping malformed config from reaching
a visitor's browser and breaking the widget silently.

LEADS
-----
Form submissions are stored as leads. Storage is the same small-JSON-
under-a-lock pattern used by faq_store and catalog: no new infrastructure
to deploy, consistent with everything else in this app.

That pattern has a real ceiling. Leads are the one thing here that an
operator will be upset to lose, and a JSON file rewritten on every append
is neither durable nor concurrent-safe under real load. For a single-
tenant deployment behind a support widget this is fine, and it matches
the rest of the app rather than introducing a second storage story for
one feature. If lead volume becomes meaningful, move THIS module to
SQLite (conversations.py already carries a sqlite dependency and its
pattern can be copied directly) before moving anything else.

Notification is deliberately not implemented. A form that promises "we'll
get back to you" and silently files the response is worse than one that
does not — but wiring SMTP means credentials, retries, and a bounce path,
which is a feature in its own right, not a line in this module. The
notify_email field is stored and surfaced to the admin so the console can
say plainly that leads are collected in the console and not emailed yet.
"""
import json
import os
import re
import threading
import uuid
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.getenv("WIDGET_CONFIG_PATH", "widget_config.json")
_LEADS_PATH = os.getenv("WIDGET_LEADS_PATH", "widget_leads.json")
_config_lock = threading.Lock()
_leads_lock = threading.Lock()

# What an opening button is allowed to do. The widget switches on these,
# so an unknown value would render a dead button.
INTRO_ACTIONS = ("product", "general", "sales", "support")

# Form field types the widget knows how to render.
FIELD_TYPES = ("text", "email", "textarea", "tel")

MAX_LABEL = 80
MAX_WELCOME = 400
MAX_INTRO_OPTIONS = 6
MAX_FORM_FIELDS = 8
MAX_LEADS = int(os.getenv("MAX_LEADS", "5000"))

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

_DEFAULT = {
    "name": "Support",
    "welcome": "Hi — I can answer questions from our product documentation. "
               "What are you looking for?",
    "color": "#9184d9",
    "icon_url": "",
    "intro_options": [
        {"id": "opt_product", "label": "Ask about a product", "action": "product"},
        {"id": "opt_general", "label": "Ask a general question", "action": "general"},
        {"id": "opt_sales", "label": "Talk to sales", "action": "sales"},
    ],
    "sales_form": {
        "title": "Talk to sales",
        "allow_summary": True,
        "notify_email": "",
        "fields": [
            {"id": "f_name", "label": "Your name", "type": "text", "required": True},
            {"id": "f_email", "label": "Email", "type": "email", "required": True},
            {"id": "f_msg", "label": "What are you looking for?", "type": "textarea", "required": False},
        ],
    },
    "support_form": {
        "title": "Talk to support",
        "allow_summary": True,
        "notify_email": "",
        "fields": [
            {"id": "s_name", "label": "Your name", "type": "text", "required": True},
            {"id": "s_email", "label": "Email", "type": "email", "required": True},
            {"id": "s_serial", "label": "Serial number (if known)", "type": "text", "required": False},
            {"id": "s_msg", "label": "What has gone wrong?", "type": "textarea", "required": False},
        ],
    },
}


# ── config ────────────────────────────────────────────────────────────

def _load() -> dict:
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH) as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                # Merge over defaults so a config written by an older
                # version keeps working when new keys are added.
                merged = json.loads(json.dumps(_DEFAULT))
                merged.update(data)
                return merged
        except Exception as e:
            logger.warning(f"widget config read failed, using defaults: {e}")
    return json.loads(json.dumps(_DEFAULT))


def _save_raw(data: dict) -> None:
    with open(_CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _clean_label(v, fallback: str = "") -> str:
    return (str(v or fallback)).strip()[:MAX_LABEL]


def _clean_field(f: dict, prefix: str) -> dict:
    ftype = f.get("type") if f.get("type") in FIELD_TYPES else "text"
    return {
        "id": str(f.get("id") or f"{prefix}_{uuid.uuid4().hex[:8]}")[:40],
        "label": _clean_label(f.get("label"), "Field"),
        "type": ftype,
        "required": bool(f.get("required", False)),
    }


def _clean_form(form: dict, default: dict, prefix: str) -> dict:
    form = form if isinstance(form, dict) else {}
    fields = form.get("fields")
    if not isinstance(fields, list) or not fields:
        fields = default["fields"]
    cleaned = [_clean_field(f, prefix) for f in fields[:MAX_FORM_FIELDS]
               if isinstance(f, dict)]
    if not cleaned:
        cleaned = [_clean_field(f, prefix) for f in default["fields"]]
    email = (str(form.get("notify_email") or "")).strip()[:200]

    # A reply has to go somewhere, so the visitor's email address is not
    # optional: without it a submission is a dead end that looks like it
    # worked. Rather than trusting the admin to remember, the form is
    # repaired here -- if no email field survived cleaning, one is added,
    # and whatever email field exists is forced to required.
    if not any(f["type"] == "email" for f in cleaned):
        if len(cleaned) >= MAX_FORM_FIELDS:
            cleaned = cleaned[:MAX_FORM_FIELDS - 1]
        cleaned.insert(min(1, len(cleaned)),
                       {"id": f"{prefix}_email", "label": "Email",
                        "type": "email", "required": True})
    for f in cleaned:
        if f["type"] == "email":
            f["required"] = True

    return {
        "title": _clean_label(form.get("title"), default["title"]),
        "allow_summary": bool(form.get("allow_summary", True)),
        "notify_email": email,
        # Shown to the visitor as an alternative to waiting for a reply.
        # Free text, not validated as a number: extensions, country codes and
        # "0800 xxx (Mon-Fri 9-5)" are all legitimate here.
        "phone": (str(form.get("phone") or "")).strip()[:60],
        # CC the visitor on the eventual reply, so they hold the thread and
        # can chase it without going through the widget again.
        "cc_visitor": bool(form.get("cc_visitor", True)),
        "fields": cleaned,
    }


def validate(payload: dict) -> dict:
    """Normalize an admin-submitted config. Raises ValueError on input
    that cannot be coerced into something the widget can render."""
    if not isinstance(payload, dict):
        raise ValueError("config must be an object")

    color = (payload.get("color") or _DEFAULT["color"]).strip()
    if not _HEX_RE.match(color):
        raise ValueError(f"color must be a 6-digit hex value like #9184d9 (got '{color}')")

    opts_in = payload.get("intro_options")
    if not isinstance(opts_in, list):
        opts_in = _DEFAULT["intro_options"]
    opts = []
    for o in opts_in[:MAX_INTRO_OPTIONS]:
        if not isinstance(o, dict):
            continue
        action = o.get("action")
        if action not in INTRO_ACTIONS:
            raise ValueError(
                f"unknown option action '{action}' — expected one of {', '.join(INTRO_ACTIONS)}")
        label = _clean_label(o.get("label"))
        if not label:
            continue
        opts.append({"id": str(o.get("id") or f"opt_{uuid.uuid4().hex[:8]}")[:40],
                     "label": label, "action": action})
    if not opts:
        raise ValueError("at least one opening option is required")

    name = _clean_label(payload.get("name"), _DEFAULT["name"])
    if not name:
        raise ValueError("assistant name is required")

    return {
        "name": name,
        "welcome": (str(payload.get("welcome") or _DEFAULT["welcome"])).strip()[:MAX_WELCOME],
        "color": color,
        "icon_url": (str(payload.get("icon_url") or "")).strip()[:500],
        "intro_options": opts,
        "sales_form": _clean_form(payload.get("sales_form"), _DEFAULT["sales_form"], "f"),
        "support_form": _clean_form(payload.get("support_form"), _DEFAULT["support_form"], "s"),
    }


def get() -> dict:
    """Full config, including admin-only fields. Console read."""
    return _load()


def save(payload: dict) -> dict:
    """Validate and persist. Admin write."""
    clean = validate(payload)
    clean["updated_at"] = datetime.now(timezone.utc).isoformat()
    with _config_lock:
        _save_raw(clean)
    return clean


def reset() -> dict:
    with _config_lock:
        data = json.loads(json.dumps(_DEFAULT))
        _save_raw(data)
    return data


def public_config() -> dict:
    """What the embedded widget is served. Strips admin-only fields —
    see the TWO AUDIENCES note in the module docstring."""
    c = _load()

    def _pub_form(f: dict) -> dict:
        return {
            "title": f.get("title", ""),
            "allow_summary": bool(f.get("allow_summary", True)),
            # Whether a destination is configured, never the address itself.
            # The widget needs to know if a submission will actually reach
            # anyone; publishing the address would hand every scraper on
            # every embedding page a support inbox to spam.
            "routed": bool((f.get("notify_email") or "").strip()),
            # Published deliberately: a phone number for customers to call is
            # meant to be seen, unlike the internal notify address above.
            "phone": f.get("phone", ""),
            "cc_visitor": bool(f.get("cc_visitor", True)),
            "fields": [
                {"id": x.get("id"), "label": x.get("label"),
                 "type": x.get("type"), "required": bool(x.get("required"))}
                for x in f.get("fields", [])
            ],
        }

    return {
        "name": c.get("name", _DEFAULT["name"]),
        "welcome": c.get("welcome", _DEFAULT["welcome"]),
        "color": c.get("color", _DEFAULT["color"]),
        "icon_url": c.get("icon_url", ""),
        "intro_options": c.get("intro_options", []),
        "sales_form": _pub_form(c.get("sales_form", _DEFAULT["sales_form"])),
        "support_form": _pub_form(c.get("support_form", _DEFAULT["support_form"])),
    }


# ── leads ─────────────────────────────────────────────────────────────

def _load_leads() -> list[dict]:
    if os.path.exists(_LEADS_PATH):
        try:
            with open(_LEADS_PATH) as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception as e:
            logger.warning(f"leads read failed: {e}")
    return []


def _save_leads(items: list[dict]) -> None:
    with open(_LEADS_PATH, "w") as f:
        json.dump(items, f, indent=2)


MAX_ENQUIRY_CHARS = 4000

# What produced the enquiry body attached to a lead. Stored so the console
# can say whether a human wrote it or a model drafted it — a staff member
# replying to a model-drafted enquiry should know that is what they are
# reading.
SUMMARY_SOURCES = ("chat", "written", "none")


def add_lead(kind: str, values: dict, product: str | None = None,
             transcript: list | None = None, enquiry: str = "",
             summary_source: str = "none") -> dict:
    """Record a form submission from the widget.

    `values` is matched against the CONFIGURED fields for this form —
    keys that are not configured fields are dropped rather than stored.
    A public endpoint that writes arbitrary caller-supplied keys into a
    file the admin console renders is an obvious abuse path, and dropping
    unknown keys closes it without needing to guess at intent.

    `enquiry` is the written-up version of what the visitor wants — either
    drafted from the chat, or typed by them. `summary_source` records which,
    because "a model wrote this" is something the person replying needs to
    know. Neither is trusted: both are stored as text and rendered escaped.
    """
    if kind not in ("sales", "support"):
        raise ValueError("kind must be 'sales' or 'support'")

    cfg = _load()
    form = cfg.get(f"{kind}_form", _DEFAULT[f"{kind}_form"])
    allowed = {f["id"]: f for f in form.get("fields", [])}

    clean_values = {}
    for fid, field in allowed.items():
        raw = values.get(fid, "")
        text = (str(raw) if raw is not None else "").strip()[:2000]
        if field.get("required") and not text:
            raise ValueError(f"'{field['label']}' is required")
        if text:
            clean_values[fid] = {"label": field["label"], "value": text}

    if not clean_values:
        raise ValueError("form was empty")

    if summary_source not in SUMMARY_SOURCES:
        summary_source = "none"
    enquiry_text = (str(enquiry or "")).strip()[:MAX_ENQUIRY_CHARS]
    # A form with the summary option turned off must not carry one anyway,
    # whatever the caller sent — the setting is the admin's decision about
    # what this form collects, and a public caller does not get to override it.
    if not form.get("allow_summary"):
        enquiry_text = ""
        summary_source = "none"
    if not enquiry_text:
        summary_source = "none"

    # The address to CC the visitor on, pulled from whichever field is the
    # email one rather than asked for twice. Stored separately from `values`
    # so the send path does not have to re-discover which field held it.
    cc_email = ""
    if form.get("cc_visitor", True):
        for fid, field in allowed.items():
            if field.get("type") == "email" and fid in clean_values:
                cc_email = clean_values[fid]["value"]
                break

    lead = {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "product": product or "",
        "values": clean_values,
        "cc_email": cc_email,
        "enquiry": enquiry_text,
        "summary_source": summary_source,
        "transcript": (transcript or [])[-20:] if form.get("allow_summary") else [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "handled": False,
        # Recorded so the console can be honest that nothing was emailed.
        "notify_email": form.get("notify_email", ""),
        "notified": False,
    }
    with _leads_lock:
        items = _load_leads()
        items.append(lead)
        if len(items) > MAX_LEADS:
            items = items[len(items) - MAX_LEADS:]
        _save_leads(items)
    return lead


def list_leads(kind: str | None = None, include_handled: bool = True,
               limit: int = 200) -> list[dict]:
    items = _load_leads()
    if kind:
        items = [l for l in items if l.get("kind") == kind]
    if not include_handled:
        items = [l for l in items if not l.get("handled")]
    items.sort(key=lambda l: l.get("created_at", ""), reverse=True)
    return items[:limit]


def mark_lead(lead_id: str, handled: bool = True) -> bool:
    with _leads_lock:
        items = _load_leads()
        for l in items:
            if l.get("id") == lead_id:
                l["handled"] = bool(handled)
                _save_leads(items)
                return True
    return False


def delete_lead(lead_id: str) -> bool:
    with _leads_lock:
        items = _load_leads()
        n = len(items)
        items = [l for l in items if l.get("id") != lead_id]
        _save_leads(items)
        return len(items) < n


def lead_stats() -> dict:
    items = _load_leads()
    return {
        "total": len(items),
        "unhandled": len([l for l in items if not l.get("handled")]),
        "sales": len([l for l in items if l.get("kind") == "sales"]),
        "support": len([l for l in items if l.get("kind") == "support"]),
    }
