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


def _remove(p):
    try:
        os.remove(p)
    except OSError:
        pass   # gone already, or another waiter has it open for a moment; whoever finds it unlocked removes it


def _alive(ticket, grab):
    """A queue ticket is alive while its waiter holds the lock on it; a crashed waiter's ticket is removed."""
    try:
        f = open(ticket, "a+")
    except OSError:
        return False
    f.seek(0)
    try:
        grab(f)
    except OSError:
        f.close()
        return True
    f.close()
    _remove(ticket)
    return False


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
    # Waiters take the lab in arrival order: each holds a lock on its own ticket in <lock>.queue and tries the lab only
    # when no live ticket is older. Without it a job running pass after pass re-took the lab within a second of each
    # release, and a gate polling every 20 s waited 3.5 hours behind R&D's three arms (2026-11-03).
    queue = path + ".queue"
    os.makedirs(queue, exist_ok=True)
    mine = f"{time.time_ns():020d}-{os.getpid()}"
    ticket = open(os.path.join(queue, mine), "a+")
    ticket.seek(0)
    grab(ticket)
    waited = 0
    while True:
        if not any(_alive(os.path.join(queue, t), grab) for t in sorted(os.listdir(queue)) if t < mine):
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
    ticket.close()
    _remove(os.path.join(queue, mine))
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
    # arrival order: A queues first and polls slowly, B queues behind it polling fast; A still goes first.
    # A crashed waiter's ticket (nobody holds it) is skipped and removed.
    stale = os.path.join(env["GO_LAB_LOCK"] + ".queue", "0" * 19 + "1-1")
    open(stale, "w").close()
    order = os.path.join(tmp, "order")
    job = lambda name, poll, hold: subprocess.Popen([sys.executable, "-c",
        f"import sys, time; sys.path.insert(0, {here!r}); import lab_lock; h = lab_lock.lab_lock(poll={poll}); "
        f"print('held', flush=True); open({order!r}, 'a').write({name!r}); time.sleep({hold})"],
        env=env, stdout=subprocess.PIPE, text=True)
    first = job("H", 1, 4)
    assert first.stdout.readline().strip() == "held"
    a = job("A", 1, 1)
    assert "waiting" in a.stdout.readline()
    b = job("B", 0.1, 0)
    assert first.wait() == a.wait() == b.wait() == 0
    assert open(order).read() == "HAB", open(order).read()
    assert os.listdir(env["GO_LAB_LOCK"] + ".queue") == [], os.listdir(env["GO_LAB_LOCK"] + ".queue")
    print("lab_lock selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    elif len(sys.argv) < 2:
        sys.exit(__doc__)
    else:
        sys.exit(run(sys.argv[1:]))
