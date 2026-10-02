"""The backup escalation and the console send the model the console chose.

Observed 2026-09-27 with the "Company keys" (openai) slot pointed at the
on-prem gateway: ONLINE_OPENAI_MODEL and MODEL_ROLE_BACKUP were both
itl-gpt-flash, yet every backup escalation sent "gpt-4o-mini" (a hardcoded
default the gateway answers 400 for), 7 of 7 attempts in one live run.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import providers  # noqa: E402

KEYS = ("ONLINE_OPENAI_MODEL", "ONLINE_DEEPSEEK_MODEL", "MODEL_ROLE_BACKUP")


def _with_env(**env):
    old = {k: os.environ.get(k) for k in KEYS}

    def restore():
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    for k in KEYS:
        os.environ.pop(k, None)
    os.environ.update(env)
    return restore


def test_default_model_follows_the_configured_model():
    restore = _with_env(ONLINE_OPENAI_MODEL="itl-gpt-flash", ONLINE_DEEPSEEK_MODEL="deepseek-v4-flash")
    try:
        assert providers._default_model_for("openai") == "itl-gpt-flash"
        assert providers._default_model_for("deepseek") == "deepseek-v4-flash"
        assert providers._default_model_for("local") == "mistral"
    finally:
        restore()


def test_unconfigured_providers_keep_their_static_default():
    restore = _with_env()
    try:
        assert providers._default_model_for("openai") == "gpt-4o-mini"
        assert providers._default_model_for("deepseek") == "deepseek-v4-flash"
        assert providers._default_model_for("nonsense") == ""
    finally:
        restore()


def test_backup_escalation_uses_the_backup_roles_model():
    import inspect
    import keystore
    import main
    src = inspect.getsource(main.query)
    assert 'keystore.model_for_role("backup", _backup_provider)' in src
    restore = _with_env(ONLINE_OPENAI_MODEL="itl-gpt-flash", MODEL_ROLE_BACKUP="itl-gpt-pro")
    try:
        assert keystore.model_for_role("backup", "openai") == "itl-gpt-pro"
        os.environ.pop("MODEL_ROLE_BACKUP")
        assert keystore.model_for_role("backup", "openai") == "itl-gpt-flash"
    finally:
        restore()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
