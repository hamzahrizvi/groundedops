"""
Tests for keyvault — encrypted-at-rest storage of the DeepSeek API key.

These run against a temporary vault path so they never touch a real
saved key. No network, no crypto secrets required beyond what the
module derives locally.
"""

import json
import os
import tempfile

import keyvault


def _isolate(tmpdir):
    """Point keyvault at a temp file for the duration of a test."""
    keyvault._VAULT_FILE = os.path.join(tmpdir, ".deepseek_key.enc")


def test_save_load_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        _isolate(d)
        keyvault.save_key("sk-roundtrip-123")
        assert keyvault.has_key() is True
        assert keyvault.load_key() == "sk-roundtrip-123"


def test_key_is_not_plaintext_on_disk():
    with tempfile.TemporaryDirectory() as d:
        _isolate(d)
        secret = "sk-super-secret-value-987"
        keyvault.save_key(secret)
        with open(keyvault._VAULT_FILE, "rb") as f:
            raw = f.read()
        # The literal key must NOT appear anywhere in the stored bytes.
        assert secret.encode() not in raw
        assert len(raw) > 0


def test_clear_removes_key():
    with tempfile.TemporaryDirectory() as d:
        _isolate(d)
        keyvault.save_key("sk-to-clear")
        keyvault.clear_key()
        assert keyvault.has_key() is False
        assert keyvault.load_key() is None


def test_has_key_false_when_never_set():
    with tempfile.TemporaryDirectory() as d:
        _isolate(d)
        assert keyvault.has_key() is False
        assert keyvault.load_key() is None


def test_save_empty_key_rejected():
    with tempfile.TemporaryDirectory() as d:
        _isolate(d)
        raised = False
        try:
            keyvault.save_key("   ")
        except ValueError:
            raised = True
        assert raised is True


def test_corrupt_vault_returns_none_not_crash():
    with tempfile.TemporaryDirectory() as d:
        _isolate(d)
        with open(keyvault._VAULT_FILE, "wb") as f:
            f.write(b"not-a-valid-fernet-token")
        # Undecryptable (e.g. copied from another machine) -> None, no raise.
        assert keyvault.load_key() is None
        assert keyvault.has_key() is False


def test_legacy_plaintext_migration():
    with tempfile.TemporaryDirectory() as d:
        _isolate(d)
        legacy_path = os.path.join(d, ".deepseek_key.json")
        with open(legacy_path, "w", encoding="utf-8") as f:
            json.dump({"key": "sk-legacy-plaintext"}, f)

        migrated = keyvault.migrate_legacy_plaintext(legacy_path)

        assert migrated is True
        # Plaintext file is gone...
        assert not os.path.exists(legacy_path)
        # ...and the key is now retrievable from the encrypted vault.
        assert keyvault.load_key() == "sk-legacy-plaintext"
