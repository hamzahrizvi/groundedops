"""Widget defaults must not name a company or promise more than the bot does.

Static checks on the shared widget JS: defaults, and that the header name and
letter avatar follow the console's company name once config loads.
"""
import pathlib
import re

import _harness  # noqa: F401

JS = (pathlib.Path(__file__).resolve().parent.parent / "widget" / "groundedops-widget.js").read_text(encoding="utf-8")


def _apply_server_config():
    i = JS.index("function applyServerConfig")
    return JS[i:JS.index("var configLoaded", i)]


def test_default_agent_is_not_assistant():
    assert 'attr("data-agent-name", "Assistant")' not in JS
    assert 'attr("data-agent-name", "Support")' in JS


def test_default_launcher_label():
    line = next(l for l in JS.splitlines() if "launcherLabel:" in l)
    assert '"Ask about our products"' in line
    assert "Ask a question" not in line


def test_default_welcome_names_no_company():
    line = next(l for l in JS.splitlines() if "welcome: attr(" in l)
    assert "Innovative" not in line


def test_server_name_updates_header_and_letter_avatar():
    src = _apply_server_config()
    assert '.go-hname' in src
    assert re.search(r'\.go-av,\s*\.go-mav', src)
    assert src.index("icon_url") < src.index(".go-hname"), "an icon must win"


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
    print("ok")
