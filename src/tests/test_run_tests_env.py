"""An own-process test child must not inherit provider keys or the admin
password from the parent (main.py would otherwise load the real .env)."""
# run-in-own-process
import os
import subprocess
import sys

import run_tests


def test_child_sees_keys_blank():
    old = {n: os.environ.get(n) for n in run_tests.LEAKY_ENV}
    try:
        for n in run_tests.LEAKY_ENV:
            os.environ[n] = "real"
        e = run_tests.own_process_env()
        assert all(e[n] == "" for n in run_tests.LEAKY_ENV)
        r = subprocess.run([sys.executable, "-c",
                            "import os,sys;sys.exit(any(os.environ.get(n) for n in sys.argv[1:]))",
                            *run_tests.LEAKY_ENV], env=e)
        assert r.returncode == 0
    finally:
        for n, v in old.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v
