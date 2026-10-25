"""An own-process test child must not inherit secrets from the parent, nor
load them from src/.env (main.py would otherwise load the real .env)."""
# run-in-own-process
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import run_tests

NAMES = ("ADMIN_PASSWORD", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
         "SESSION_SECRET", "WIDGET_TOKEN_SECRET", "BACKUP_PASSPHRASE", "CONFLUENCE_API_TOKEN")


def _restore(old):
    for n, v in old.items():
        if v is None:
            os.environ.pop(n, None)
        else:
            os.environ[n] = v


def test_child_sees_secrets_blank():
    old = {n: os.environ.get(n) for n in NAMES + ("GO_TEST_UNRELATED",)}
    try:
        for n in NAMES:
            os.environ[n] = "real"
        os.environ["GO_TEST_UNRELATED"] = "keep"
        e = run_tests.own_process_env()
        assert all(e[n] == "" for n in NAMES)
        assert e["GO_TEST_UNRELATED"] == "keep"
        assert e["PATH"] == os.environ["PATH"]
        assert e["PYTHONPATH"].startswith(str(Path(run_tests.__file__).parent))
        r = subprocess.run([sys.executable, "-c",
                            "import os,sys;sys.exit(any(os.environ.get(n) for n in sys.argv[1:]))",
                            *NAMES], env=e)
        assert r.returncode == 0
    finally:
        _restore(old)


def test_names_only_in_env_file_are_blanked():
    only = ("KAPA_API_KEY", "FIN_API_KEY", "NEW_THING_SECRET")
    old = {n: os.environ.get(n) for n in only}
    saved = run_tests.MAIN_ENV_FILE
    tmp = tempfile.mkdtemp()
    try:
        f = Path(tmp) / ".env"
        f.write_text('KAPA_API_KEY=x\nFIN_API_KEY=x\n# OLD_SECRET=x\n'
                     'CONFLUENCE_EMAIL=a@b\n  NEW_THING_SECRET = "x"\n', encoding="utf-8")
        run_tests.MAIN_ENV_FILE = f
        for n in only:
            os.environ.pop(n, None)
        e = run_tests.own_process_env()
        assert all(e[n] == "" for n in only)
        assert "OLD_SECRET" not in e
        assert e.get("CONFLUENCE_EMAIL") == os.environ.get("CONFLUENCE_EMAIL")
    finally:
        run_tests.MAIN_ENV_FILE = saved
        _restore(old)
        shutil.rmtree(tmp, ignore_errors=True)
