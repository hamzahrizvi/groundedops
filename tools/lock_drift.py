"""Which lock pins the running interpreter does not have (D-20261008-17).
python tools/lock_drift.py [lock]  -> exit 1 and list them; the .exe spec calls drift()."""
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from packaging.requirements import Requirement
from packaging.version import Version

LOCK = Path(__file__).resolve().parent.parent / "src" / "requirements.lock"

def drift(lock=LOCK):
    off = []
    for line in Path(lock).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        req = Requirement(line)
        if req.marker and not req.marker.evaluate():
            continue
        want = next(iter(req.specifier)).version
        try:
            have = version(req.name)
        except PackageNotFoundError:
            have = None
        if have is None or Version(have) != Version(want):
            off.append(f"{req.name}: installed {have or 'none'}, lock {want}")
    return off

if __name__ == "__main__":
    off = drift(sys.argv[1] if len(sys.argv) > 1 else LOCK)
    print("\n".join(off) or "venv matches the lock")
    sys.exit(1 if off else 0)
