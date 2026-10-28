"""Known holes in the packages, weekly (D-20261021-13). Run by Audit.
  REPO/.venv/Scripts/python.exe tools/dep_audit.py [--ops OPS] [--repo REPO]
  python tools/dep_audit.py --selftest
Audits REPO/src/requirements.lock and REPO/.venv with pip-audit (ignore ids read from
REPO/.github/workflows/ci.yml), adds REPO/tools/lock_drift.py's venv-vs-lock lines, prints one
MISMATCH line each, keeps the result in OPS/state/dep_audit.json, and writes
OPS/inbox/security/<YYYYMMDD>-dep-audit.md when an advisory id or drift line is new since that file.
Exit 0 nothing found, 1 MISMATCH lines, 2 could not run (state left unchanged)."""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

OFFICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ignore_ids(ci_text):
    out = []
    for line in ci_text.splitlines():
        if line.strip().startswith("#"):
            continue
        for i in re.findall(r"--ignore-vuln\s+(\S+)", line):
            if i not in out:
                out.append(i)
    return out


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if p.returncode not in (0, 1):
        raise RuntimeError(f"{os.path.basename(cmd[1])} failed ({p.returncode}): {p.stderr.strip()[-500:]}")
    return p


def audit(py, args, ignores):
    p = _run([py, "-m", "pip_audit", *args, "--format", "json",
              *sum((["--ignore-vuln", i] for i in ignores), [])])
    try:
        data = json.loads(p.stdout)
    except ValueError:
        raise RuntimeError(f"pip-audit failed ({p.returncode}): {p.stderr.strip()[-500:]}")
    return [{"name": d["name"], "version": d["version"], "id": v["id"],
             "fix": ", ".join(v.get("fix_versions") or []) or "none"}
            for d in data["dependencies"] for v in d.get("vulns", [])]


def drift(py, repo):
    p = _run([py, os.path.join(repo, "tools", "lock_drift.py"),
              os.path.join(repo, "src", "requirements.lock")])
    return [x for x in p.stdout.splitlines() if x.strip()] if p.returncode == 1 else []


def run(repo):
    py = os.path.join(repo, ".venv", "Scripts", "python.exe")
    with open(os.path.join(repo, ".github", "workflows", "ci.yml"), encoding="utf-8") as f:
        ign = ignore_ids(f.read())
    return {"ignored": ign,
            "lock": audit(py, ["-r", os.path.join(repo, "src", "requirements.lock"), "--no-deps", "--disable-pip"], ign),
            "venv": audit(py, [], ign),
            "drift": drift(py, repo)}


def keys(result):
    return ({f"{s} {r['name']} {r['id']}" for s in ("lock", "venv") for r in result.get(s, [])}
            | {"drift " + d for d in result.get("drift", [])})


def lines(result):
    out = [f"MISMATCH {s:5} {r['name']} {r['version']} fix {r['fix']} {r['id']}"
           for s in ("lock", "venv") for r in result.get(s, [])]
    return out + [f"MISMATCH drift {d}" for d in result.get("drift", [])]


def record(ops, result, today):
    state = os.path.join(ops, "state", "dep_audit.json")
    prev, when = set(), "no earlier run"
    if os.path.exists(state):
        with open(state, encoding="utf-8") as f:
            old = json.load(f)
        prev, when = keys(old), old.get("when", when)
    new = sorted(keys(result) - prev)
    path = None
    if new:
        d = os.path.join(ops, "inbox", "security")
        os.makedirs(d, exist_ok=True)
        path, n = os.path.join(d, f"{today}-dep-audit.md"), 1
        while os.path.exists(path):
            n += 1
            path = os.path.join(d, f"{today}-dep-audit-{n}.md")
        day = f"{today[:4]}-{today[4:6]}-{today[6:]}"
        body = (f"# Dependency audit {day}: {len(new)} new advisory or drift line(s)\n\n"
                "Severity: medium\n\n"
                "- From: audit, tools/dep_audit.py (weekly)\n"
                "- Type of question / area: third-party packages (src/requirements.lock and REPO/.venv)\n\n"
                "## What goes wrong\n"
                f"New since the last dependency audit ({when}):\n"
                + "".join(f"- {k}\n" for k in new)
                + "\n## Evidence\n"
                + "".join(f"- {x}\n" for x in lines(result))
                + f"Ignored as in ci.yml: {', '.join(result['ignored']) or 'none'}\n\n"
                "## Suggested direction\n"
                "Bump each package to its fix version in src/requirements.lock (hashed, --require-hashes) "
                "and the venv; drift lines mean the venv and the lock disagree. "
                "Severity is the tool's default; triage sets the real one.\n\n"
                "## How to verify\n"
                "`REPO/.venv/Scripts/python.exe OFFICE/tools/dep_audit.py` prints no MISMATCH line for these.\n")
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
    os.makedirs(os.path.dirname(state), exist_ok=True)
    with open(state, "w", encoding="utf-8") as f:
        json.dump({"when": time.strftime("%Y-%m-%dT%H:%M:%S"), **result}, f, indent=1)
    return path


def selftest():
    assert ignore_ids("# use --ignore-vuln <ID> here\n  run: x --ignore-vuln TEST-9 --ignore-vuln TEST-8\n") == ["TEST-9", "TEST-8"]
    with tempfile.TemporaryDirectory() as tmp:
        r1 = {"ignored": [], "lock": [{"name": "pkg-a", "version": "1.0", "id": "TEST-0001", "fix": "1.1"}],
              "venv": [], "drift": []}
        os.makedirs(os.path.join(tmp, "state"))
        with open(os.path.join(tmp, "state", "dep_audit.json"), "w", encoding="utf-8") as f:
            json.dump({"when": "2026-01-01T00:00:00", **r1}, f)
        assert record(tmp, r1, "20260101") is None
        inbox = os.path.join(tmp, "inbox", "security")
        assert not os.path.isdir(inbox) or not os.listdir(inbox)
        r2 = {**r1, "lock": r1["lock"] + [{"name": "pkg-b", "version": "2.0", "id": "TEST-0002", "fix": "none"}]}
        p = record(tmp, r2, "20260101")
        assert p.endswith("20260101-dep-audit.md")
        text = open(p, encoding="utf-8").read()
        assert "lock pkg-b TEST-0002" in text and text.count("TEST-0001") == 1
        assert record(tmp, r2, "20260101") is None
        r3 = {**r2, "drift": ["pkg-c: installed none, lock 2.0"]}
        p3 = record(tmp, r3, "20260101")
        assert p3.endswith("20260101-dep-audit-2.md")
        assert "drift pkg-c" in open(p3, encoding="utf-8").read()
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", default="C:/Users/hrizvi/groundedops-ops")
    ap.add_argument("--repo", default=os.path.normpath(os.path.join(OFFICE, "..", "..", "..")))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    try:
        result = run(a.repo)
    except (OSError, RuntimeError) as e:
        print(f"dep_audit could not run: {e}")
        sys.exit(2)
    out = lines(result)
    for x in out:
        print(x)
    path = record(a.ops, result, time.strftime("%Y%m%d"))
    print(f"{len(out)} mismatch(es); ignored {', '.join(result['ignored']) or 'none'}; new finding: {path or 'none'}")
    sys.exit(1 if out else 0)


if __name__ == "__main__":
    main()
