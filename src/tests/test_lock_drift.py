import os, sys, tempfile
from importlib.metadata import version
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
from lock_drift import drift

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
