"""The lab lock: one measuring job at a time on this machine (D-20261010-04).

blind_eval.py's "system" phase times every answer; anything else loading the models or
running the test suite on the same CPU meanwhile inflates those timings (runs/20261010:
median 6.8 -> 14.8 s while Dev's full suite ran). blind_eval.py takes this lock for its
system phase; Dev runs its test suites and probes through it, so each waits for the other.

  python tools/lab_lock.py <command> [args...]   run a command holding the lock, exit with its code
  python tools/lab_lock.py --selftest

The command gets GO_LAB_LOCK_HELD=1, so a blind_eval.py started under it does not wait for
its own parent. GO_LAB_LOCK overrides the lock file (the self-test uses it).

Unless already set, the command also gets LOG_FILE and FAQ_GAP_PATH in LAB_DIR
(<temp>/groundedops-lab), so a probe or test that imports main from REPO/src never writes the
live src/logs.jsonl or src/faq_gaps.json (D-20261010-20). Read a probe's app log there.
"""
import os, subprocess, sys, tempfile, time

HELD = "GO_LAB_LOCK_HELD"
LAB_DIR = os.path.join(tempfile.gettempdir(), "groundedops-lab")


def lab_lock(poll=20):
    """Waits for the lock; returns (handle, seconds waited). Keep the handle for the life of the
    process: the OS releases the lock when the process ends, so a crashed run never leaves it
    held. Returns (None, 0) when a parent process already holds it."""
    if os.environ.get(HELD):
        return None, 0
    try:
        import msvcrt
        grab = lambda f: msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except ImportError:
        import fcntl
        grab = lambda f: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    path = os.environ.get("GO_LAB_LOCK") or os.path.expanduser("~/.groundedops-lab.lock")
    waited = 0
    while True:
        f = open(path, "a+")   # a fresh handle each try: a failed msvcrt attempt never succeeds on retry
        f.seek(0)
        try:
            grab(f)
            break
        except OSError:
            f.close()
            if not waited:
                print("waiting for the lab: another measuring job holds the lab lock", flush=True)
            time.sleep(poll)
            waited += poll
    if waited:
        print(f"lab free after {waited // 60} min", flush=True)
    return f, waited


def run(cmd):
    handle, _ = lab_lock()   # held until this process exits
    os.makedirs(LAB_DIR, exist_ok=True)
    env = {**os.environ, HELD: "1"}
    env.setdefault("LOG_FILE", os.path.join(LAB_DIR, "logs.jsonl"))
    env.setdefault("FAQ_GAP_PATH", os.path.join(LAB_DIR, "faq_gaps.json"))
    return subprocess.call(cmd, env=env)


def selftest():
    tmp = tempfile.mkdtemp()
    env = {k: v for k, v in os.environ.items() if k != HELD}
    env["GO_LAB_LOCK"] = os.path.join(tmp, "lab.lock")
    done = os.path.join(tmp, "holder.done")
    here, me = os.path.dirname(os.path.abspath(__file__)), os.path.abspath(__file__)
    # stand-in for blind_eval's system phase: takes the lock the same way, holds it 5 s
    holder = subprocess.Popen([sys.executable, "-c",
        f"import sys, time; sys.path.insert(0, {here!r}); import lab_lock; h = lab_lock.lab_lock(); "
        f"print('held', flush=True); time.sleep(5); open({done!r}, 'w').close()"],
        env=env, stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    # under a parent that holds it (GO_LAB_LOCK_HELD): no wait, and the exit code comes back
    nested = subprocess.run([sys.executable, me, sys.executable, "-c", "print('nested'); raise SystemExit(3)"],
                            env={**env, HELD: "1"}, capture_output=True, text=True)
    assert nested.returncode == 3 and "nested" in nested.stdout and "waiting" not in nested.stdout, nested
    show = [sys.executable, me, sys.executable, "-c",
            "import os; print(os.environ['LOG_FILE']); print(os.environ['FAQ_GAP_PATH'])"]
    clean = {k: v for k, v in env.items() if k not in ("LOG_FILE", "FAQ_GAP_PATH")}
    out = subprocess.run(show, env={**clean, HELD: "1"}, capture_output=True, text=True).stdout.split()
    assert out == [os.path.join(LAB_DIR, "logs.jsonl"), os.path.join(LAB_DIR, "faq_gaps.json")], out
    out = subprocess.run(show, env={**clean, HELD: "1", "LOG_FILE": "mine.jsonl"},
                         capture_output=True, text=True).stdout.split()
    assert out[0] == "mine.jsonl", out          # a caller's own choice is kept
    # a Dev check started during the run waits for the run to finish
    check = subprocess.run([sys.executable, me, sys.executable, "-c",
                            f"import os; print('holder done' if os.path.exists({done!r}) else 'holder running')"],
                           env=env, capture_output=True, text=True)
    assert check.returncode == 0, check
    assert "waiting for the lab" in check.stdout and "holder done" in check.stdout, check.stdout
    assert holder.wait() == 0
    print("lab_lock selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    elif len(sys.argv) < 2:
        sys.exit(__doc__)
    else:
        sys.exit(run(sys.argv[1:]))
