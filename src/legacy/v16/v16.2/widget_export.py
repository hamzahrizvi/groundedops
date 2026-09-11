"""Build a ready-to-install WordPress plugin zip from the console.

The plugin source in widget/groundedops-widget.php is the single copy; this
module stamps the deployment-specific values into it rather than keeping a
second, divergent template. Both values it substitutes sit behind
`if (!defined(...))` guards in the source, so a wp-config.php constant still
wins — embedding a default does not take the documented path away.

Two things this exists to prevent, both of which present as "the AI does not
work" rather than as a configuration error:

  * GROUNDEDOPS_API left pointing at the example URL.
  * GROUNDEDOPS_SECRET not equal to the server's WIDGET_TOKEN_SECRET, which
    silently downgrades every signed-in visitor to a guest.

The second is why `include_secret` reads the secret from this server's own
environment instead of asking an admin to paste it: a value typed twice is a
value that can differ.
"""

from __future__ import annotations

import io
import os
import re
import time
import zipfile
from datetime import datetime, timezone

PLUGIN_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "widget", "groundedops-widget.php")

# run.ps1 / run.cmd's Cloudflare quick-tunnel step writes cloudflared's own
# output here, at the repo root, and truncates both on every start (see
# run.ps1's tunnel section) -- so the newest match in either file is the
# tunnel currently up, or, if run.ps1 has since been stopped, the last one
# that was. Checked in this order because run.ps1 itself found cloudflared's
# URL on stderr first.
TUNNEL_LOG_NAMES = ("tunnel.log.err", "tunnel.log")
_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# WordPress unpacks the archive into wp-content/plugins/<top-level-dir>, so
# the php file must sit inside a folder rather than at the archive root.
PLUGIN_DIR = "groundedops-widget"

EXAMPLE_HOST = "yourcompany.com"


class ExportError(Exception):
    """Bad input from the console — surfaced to the admin as a 400."""


def _php_quote(value: str) -> str:
    """Escape for a PHP single-quoted string literal.

    Only backslash and single quote are special inside ''. Everything else,
    including $, is literal — which is the reason to use single quotes here.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def normalise_api_url(raw: str) -> str:
    """Validate the backend URL an admin typed, and strip the trailing slash.

    Rejects rather than repairs: a silently-corrected URL that is wrong in a
    different way is harder to debug than a refusal at the point of typing.
    """
    url = (raw or "").strip().rstrip("/")
    if not url:
        raise ExportError("Enter the address visitors' browsers will use to "
                          "reach this server.")
    if not re.match(r"^https?://", url):
        raise ExportError("The address must start with https:// (or http:// "
                          "for a local test).")
    if EXAMPLE_HOST in url:
        raise ExportError(
            f"That is still the example address from the documentation. "
            f"Replace {EXAMPLE_HOST} with this deployment's real hostname.")
    if re.match(r"^http://", url) and not re.match(
            r"^http://(localhost|127\.0\.0\.1)([:/]|$)", url):
        raise ExportError(
            "Plain http would send visitor tokens in clear text. Use https, "
            "or localhost for a local test.")
    return url


def server_secret() -> str:
    """This deployment's widget signing secret, as the running app sees it.

    Read at call time rather than import time: quota.py caches it at import,
    but an export is rare and a stale value here would produce a plugin that
    is wrong in exactly the way this module exists to prevent.
    """
    return (os.getenv("WIDGET_TOKEN_SECRET") or "").strip()


def _install_notes(api_url: str, has_secret: bool, when: str) -> str:
    if has_secret:
        secret_para = (
            "The signing secret is ALREADY EMBEDDED in this build. There is\n"
            "nothing to add to wp-config.php, and nothing further to\n"
            "configure -- upload, activate, done.\n"
            "\n"
            "  *** TREAT THIS ZIP AS A PASSWORD ***\n"
            "\n"
            "  Anyone who has this file can mint tokens that impersonate a\n"
            "  signed-in customer: full AI answers, charged to your provider\n"
            "  account, bypassing the guest limits. Send it to the person\n"
            "  installing it, over something private. Do not put it on a\n"
            "  shared drive, do not email it to a list, and do not commit it.\n"
            "\n"
            "  If it does leak, rotate WIDGET_TOKEN_SECRET on the server and\n"
            "  re-export. Every old token stops working immediately.\n")
    else:
        secret_para = (
            "This build does NOT contain the signing secret, so it is safe to\n"
            "pass around. One step is left for whoever installs it -- add this\n"
            "line to wp-config.php, ABOVE \"That's all, stop editing\":\n"
            "\n"
            "    define('GROUNDEDOPS_SECRET', 'ask-the-groundedops-admin');\n"
            "\n"
            "It must equal WIDGET_TOKEN_SECRET on the GroundedOps server. If\n"
            "the two differ, the widget still appears and still answers, but\n"
            "every signed-in visitor is quietly treated as a guest -- which\n"
            "looks like \"the AI is broken\" rather than a mismatch. If it is\n"
            "left out entirely, WordPress shows a warning in the admin area.\n")

    return f"""GroundedOps Support Widget -- generated plugin
================================================

Generated {when} for:

    {api_url}

INSTALL
    1. WP Admin > Plugins > Add New > Upload Plugin
    2. Choose this zip file, install, and activate.
    3. {"Nothing else." if has_secret else "See the secret note below."}

{secret_para}
BRANDING IS NOT IN HERE
    The assistant's name, colour, icon, welcome text, opening buttons and
    the sales/support forms all live in the GroundedOps console under
    Widget design. Change them there and the site picks them up on the next
    page load -- this plugin never needs re-exporting for a look-and-feel
    change. Re-export only if the backend address or the secret changes.

ONE THING TO CHECK ON THE SERVER
    WIDGET_ALLOWED_ORIGINS must list this WordPress site's origin, or the
    browser blocks every call with a CORS error and the widget renders but
    never answers.

IF SOMETHING IS WRONG
    The plugin checks its own configuration and prints a warning in the
    WordPress admin area if the address or the secret is missing. It cannot
    detect a secret that is present but WRONG -- for that, the symptom is
    signed-in users getting guest answers.
"""


def build_plugin_zip(api_url: str, *, include_secret: bool,
                     exported_by: str = "") -> tuple[bytes, dict]:
    """Return (zip bytes, manifest).

    manifest carries what the console needs to describe the download without
    reopening it: the filename, whether it is sensitive, and the URL baked in.
    """
    url = normalise_api_url(api_url)

    secret = ""
    if include_secret:
        secret = server_secret()
        if not secret:
            raise ExportError(
                "This server has no WIDGET_TOKEN_SECRET set, so there is no "
                "secret to embed. Set it in docker/.env and restart, or export "
                "without the secret and add it to wp-config.php by hand.")

    try:
        with open(PLUGIN_SRC, encoding="utf-8") as fh:
            php = fh.read()
    except OSError as e:
        raise ExportError(f"Could not read the plugin source: {e}")

    # Anchored on the full define() line including its guard, so a partial
    # match cannot rewrite the wrong constant. Count-checked below: a source
    # edit that breaks these patterns must fail loudly here rather than ship
    # a plugin still pointing at the example host.
    php, n_api = re.subn(
        r"(?m)^(\s*define\('GROUNDEDOPS_API',\s*)'[^']*'(\);.*)$",
        lambda m: f"{m.group(1)}'{_php_quote(url)}'{m.group(2)}",
        php, count=1)
    if n_api != 1:
        raise ExportError(
            "The plugin source no longer has the expected GROUNDEDOPS_API "
            "line, so this export would ship the example address. Update "
            "widget_export.py alongside the plugin.")

    if include_secret:
        php, n_sec = re.subn(
            r"(?m)^(\s*define\('GROUNDEDOPS_SECRET',\s*)'[^']*'(\);).*$",
            lambda m: (f"{m.group(1)}'{_php_quote(secret)}'{m.group(2)}"
                       "  // embedded at export; wp-config.php still wins"),
            php, count=1)
        if n_sec != 1:
            raise ExportError(
                "The plugin source no longer has the expected "
                "GROUNDEDOPS_SECRET line. Update widget_export.py alongside "
                "the plugin.")

    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    by = f" by {exported_by}" if exported_by else ""
    # Built outside the f-strings below: a multi-line expression inside a
    # single-quoted f-string is PEP 701, which is 3.12+. CI runs 3.11.
    carries = " and the signing secret" if include_secret else ""
    secret_line = ("EMBEDDED -- treat this file as a credential"
                   if include_secret else "not included")
    banner = (
        "\n/*\n"
        f" * GENERATED BUILD -- exported from the GroundedOps console{by}\n"
        f" * on {when}, configured for:\n"
        f" *     {url}\n"
        " *\n"
        " * Edits here are lost on the next export. Change the branding and\n"
        " * the contact forms in the console under Widget design instead;\n"
        f" * this file only carries the backend address{carries}.\n"
        f" * Signing secret: {secret_line}\n"
        " */\n")
    # After the plugin header block, so WordPress still parses the metadata
    # from the first comment in the file.
    php = php.replace("if (!defined('ABSPATH')) { exit; }",
                      banner + "\nif (!defined('ABSPATH')) { exit; }", 1)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{PLUGIN_DIR}/groundedops-widget.php", php)
        z.writestr(f"{PLUGIN_DIR}/INSTALL.txt",
                   _install_notes(url, include_secret, when))

    host = re.sub(r"^https?://", "", url).split("/")[0].replace(":", "-")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    name = f"groundedops-widget-{host}-{stamp}"
    if include_secret:
        name += "-CONFIDENTIAL"

    return buf.getvalue(), {
        "filename": f"{name}.zip",
        "api_url": url,
        "secret_embedded": include_secret,
        "sensitive": include_secret,
        "generated": when,
    }


def detect_tunnel_url(root: str | None = None) -> dict:
    """Best-effort discovery of a locally running run.ps1/run.cmd Cloudflare
    quick tunnel, so the export card can offer that address instead of
    whatever this console happens to be viewed from.

    The two are NOT interchangeable: the admin console itself stays LAN-only
    through a quick tunnel (see STAGING.md), so an admin testing over the
    tunnel is necessarily viewing the console from a LAN address while the
    address the WIDGET needs is the tunnel's. Reading location.origin alone
    gets that backwards.

    Silent when nothing is found, which is the normal case for a real
    deployment: Docker and a real staging/production host never write these
    files, so returning {"url": None, ...} there is a no-op, not an error.
    There is no live signal here that distinguishes "tunnel still running"
    from "run.ps1 was stopped and this is the last URL it had" -- that is
    what age_seconds is for, so the caller can show its own age rather than
    presenting a dead address as current.

    `root` defaults to the real repo root and is only overridden by tests --
    those must never read or write the real tunnel.log, which a developer's
    own run.cmd may be actively writing to while the suite runs.
    """
    if root is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in TUNNEL_LOG_NAMES:
        path = os.path.join(root, name)
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        matches = _TUNNEL_URL_RE.findall(text)
        if not matches:
            continue
        age_seconds = None
        try:
            age_seconds = max(0, int(time.time() - os.path.getmtime(path)))
        except OSError:
            pass
        # The last match, not the first: a retried tunnel can log more than
        # one candidate before settling, and the newest is the live one.
        return {"url": matches[-1], "age_seconds": age_seconds}
    return {"url": None, "age_seconds": None}
