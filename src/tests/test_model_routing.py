"""Choosing a model in the console configures every route.

Providers were settable per job; MODELS were not settable at all. They came
from `ONLINE_<PROVIDER>_MODEL` read at call time, so choosing which model
answers meant hand-editing .env and restarting, and the console showed
nothing about what was in force.

That had a visible cost on 2026-09-22, the day the on-prem gateway came
back on the network: the ADVANCED job was pointed at it while DEFAULT was
still routed at DeepSeek, so nearly every customer question went to a
metered API while the local gateway sat idle. Nothing was misconfigured —
half the configuration had simply never been written.

The rule these tests pin is the CASCADE: a model chosen for a provider is
the baseline for every job using that provider, and a job may override it.
Clearing an override is not the same as setting it to the baseline's
current value — an inheriting job keeps following later changes, which is
what makes "pick a model once" keep working.

ENV_FILE_PATH is redirected before keystore is imported, so nothing here
can write to the real src/.env.
"""
# run-in-own-process -- this file sets ENV_FILE_PATH and blanks provider
# variables at import. In the shared interpreter those leak into every
# own-process test spawned afterwards (run_tests passes env=dict(os.environ),
# and _harness sets its own scratch path with setdefault, which will not
# override a value that is already there). Observed exactly that way: five
# unrelated files failed on "Sign-in required".
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Under /tmp, the same place _harness.py puts its scratch env, NOT the
# system TEMP that tempfile would pick on its own: keystore writes
# atomically via os.replace, and on Windows that raised PermissionError
# intermittently against a directory under the system TEMP. /tmp resolves
# to C:\tmp here and the harness has been writing its own test.env there
# all along.
#
# mkdtemp UNDER that directory, though, rather than one fixed path. It was
# "/tmp/modelrouting/test.env" for every process at once, so two suites
# running together raced on the os.replace above and the loser died with
# FileNotFoundError [WinError 2] -- its .tmp file had been consumed by the
# other process. Same shared-scratch bug _harness.py had, one level up.
import shutil
import tempfile
import atexit

os.makedirs("/tmp", exist_ok=True)
_SCRATCH_DIR = tempfile.mkdtemp(prefix="modelrouting-%d-" % os.getpid(),
                                dir="/tmp")
atexit.register(shutil.rmtree, _SCRATCH_DIR, ignore_errors=True)
_SCRATCH = os.path.join(_SCRATCH_DIR, "test.env")
os.environ["ENV_FILE_PATH"] = _SCRATCH
# Blanked, not popped: keystore reads os.environ, and a developer's real
# values would otherwise decide the outcome of every assertion below.
for _k in ("PROVIDER_ROLE_DEFAULT", "PROVIDER_ROLE_ADVANCED",
           "PROVIDER_ROLE_BACKUP", "ONLINE_OPENAI_MODEL",
           "ONLINE_DEEPSEEK_MODEL", "ONLINE_ANTHROPIC_MODEL",
           "MODEL_ROLE_DEFAULT", "MODEL_ROLE_ADVANCED", "MODEL_ROLE_BACKUP"):
    os.environ[_k] = ""

import keystore  # noqa: E402


def reset():
    for role in ("default", "advanced", "backup"):
        keystore.set_role_model(role, None)
    for prov in keystore.providers():
        keystore.set_model(prov, None)


# ── the cascade ──────────────────────────────────────────────────────

def test_choosing_a_model_sets_it_for_every_job_on_that_provider():
    """THE POINT. One choice, all the routes."""
    reset()
    keystore.set_model("openai", "itl-gpt-pro")
    for role in ("default", "advanced", "backup"):
        assert keystore.model_for_role(role, "openai") == "itl-gpt-pro"


def test_a_job_can_override_without_disturbing_the_others():
    """The deliberate split stays available: extraction on a fast model
    while deep questions go to a stronger one."""
    reset()
    keystore.set_model("openai", "itl-gpt-pro")
    keystore.set_role_model("default", "itl-gpt-flash")
    assert keystore.model_for_role("default", "openai") == "itl-gpt-flash"
    assert keystore.model_for_role("advanced", "openai") == "itl-gpt-pro"
    assert keystore.model_for_role("backup", "openai") == "itl-gpt-pro"


def test_clearing_an_override_resumes_following_the_baseline():
    """Not the same as setting the override to the baseline's current
    value — an inheriting job keeps following LATER changes, which is what
    makes choosing once keep working."""
    reset()
    keystore.set_model("openai", "itl-gpt-pro")
    keystore.set_role_model("advanced", "itl-gpt-flash")
    keystore.set_role_model("advanced", None)
    assert keystore.get_role_model("advanced") is None
    keystore.set_model("openai", "itl-gpt-superflash")
    assert keystore.model_for_role("advanced", "openai") == "itl-gpt-superflash"


def test_the_baseline_is_per_provider_not_global():
    """Switching provider must not carry the other one's model name across
    — an unknown model name is a 400 from every provider."""
    reset()
    keystore.set_model("openai", "itl-gpt-pro")
    keystore.set_model("deepseek", "deepseek-v4-flash")
    assert keystore.model_for_role("default", "openai") == "itl-gpt-pro"
    assert keystore.model_for_role("default", "deepseek") == "deepseek-v4-flash"


def test_an_unconfigured_provider_still_yields_a_usable_model():
    """A console that has never been touched must not send model=''."""
    reset()
    for prov in keystore.providers():
        assert keystore.model_for_role("default", prov)


# ── persistence and validation ───────────────────────────────────────

def test_a_choice_survives_a_restart():
    """Written to the file, not just the process — the same guarantee the
    provider assignments already give."""
    reset()
    keystore.set_model("openai", "itl-gpt-pro")
    with open(_SCRATCH, encoding="utf-8") as f:
        assert "ONLINE_OPENAI_MODEL=itl-gpt-pro" in f.read()


def test_clearing_removes_the_line_rather_than_blanking_it():
    reset()
    keystore.set_model("openai", "itl-gpt-pro")
    keystore.set_model("openai", None)
    with open(_SCRATCH, encoding="utf-8") as f:
        assert "ONLINE_OPENAI_MODEL" not in f.read()


def test_an_unknown_provider_or_role_is_refused():
    """A typo must not create a silent third setting nothing reads."""
    reset()
    for bad in ("openai-2", "", "gpt"):
        try:
            keystore.set_model(bad, "x")
            raise AssertionError(f"accepted unknown provider {bad!r}")
        except keystore.UnknownProviderError:
            pass
    try:
        keystore.set_role_model("reasoning", "x")
        raise AssertionError("accepted unknown role")
    except keystore.UnknownRoleError:
        pass


# ── the console and the request path must agree ──────────────────────

def test_llm_sends_exactly_what_the_console_shows():
    """Two readings of the same setting is how they drift. llm.py asks
    keystore rather than reading the env var itself, so the model the
    console displays is the model that goes on the wire."""
    reset()
    import llm
    keystore.set_role("default", "openai")
    keystore.set_key("openai", "test-key-not-real")
    keystore.set_model("openai", "itl-gpt-pro")
    assert llm._online_provider_model("default") == ("openai", "itl-gpt-pro")
    keystore.set_role_model("default", "itl-gpt-flash")
    assert llm._online_provider_model("default") == ("openai", "itl-gpt-flash")
    keystore.clear_key("openai")
    reset()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
