import os, sys, tempfile
from importlib.metadata import version
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
from lock_drift import drift
from unittest.mock import patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import _harness  # noqa: E402,F401 -- temp CATALOG_CONFIG, light main
import main  # noqa: E402

def _lock(text):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "x.lock")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p

def test_matching_pins_comments_and_other_platform_lines_pass():
    have = version("packaging")
    assert drift(_lock(f"packaging=={have}\n    # via x\n"
                       "packaging==0.0.1 ; sys_platform == 'no-such-os'\n")) == []

def test_wrong_version_and_missing_package_are_listed():
    off = drift(_lock("packaging==0.0.1\nno-such-package-xyz==1.0\n"))
    assert len(off) == 2
    assert off[0].startswith("packaging: installed ") and off[0].endswith("lock 0.0.1")
    assert off[1] == "no-such-package-xyz: installed none, lock 1.0"

def test_hashed_lock_lines_are_read():
    have = version("packaging")
    text = (f"packaging=={have} \\\n    --hash=sha256:{'0'*64} \\\n    --hash=sha256:{'1'*64}\n"
            f"    # via x\nno-such-package-xyz==1.0 \\\n    --hash=sha256:{'2'*64}\n")
    assert drift(_lock(text)) == ["no-such-package-xyz: installed none, lock 1.0"]

def test_startup_warns_once_naming_the_drifted_package():
    with patch.object(main.logger, "warning") as warn:
        off = main._warn_lock_drift(_lock("no-such-package-xyz==1.0\n"))
    assert off == ["no-such-package-xyz: installed none, lock 1.0"]
    assert warn.call_count == 1
    assert "no-such-package-xyz: installed none, lock 1.0" in (warn.call_args[0][0] % warn.call_args[0][1:])

def test_startup_is_silent_when_the_venv_matches():
    have = version("packaging")
    with patch.object(main.logger, "warning") as warn:
        assert main._warn_lock_drift(_lock(f"packaging=={have}\n")) == []
    assert warn.call_count == 0

def test_a_broken_check_warns_but_never_raises():
    with patch.object(main.logger, "warning") as warn:
        assert main._warn_lock_drift(_lock("this is not a requirement ===\n")) == []
    assert warn.call_count == 1
