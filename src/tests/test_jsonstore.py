"""The data-loss chain these guards exist to break.

The chain, as it stood in every store:

    a non-atomic write is interrupted   ->  the file is now corrupt
    _load() cannot parse it             ->  returns [] / seed / defaults
    the caller mutates that default     ->  cannot tell empty from unreadable
    _save() writes it back              ->  the original is destroyed

The last step is the only one that loses anything, so that is where the
guard sits. These tests assert the chain now stops there -- and, because
this is the bug that actually happened, that the specific UTF-8/cp1252
case is covered.
"""
import json
import os
import tempfile

import jsonstore


def _tmp(name="state.json"):
    return os.path.join(tempfile.mkdtemp(prefix="js-test-"), name)


# ── load: absent is fine, unreadable is loud but not fatal ──────────────

def test_load_absent_returns_default():
    """A missing file is a legitimate empty state, not an error."""
    assert jsonstore.load(_tmp(), []) == []
    assert jsonstore.load(_tmp(), {"a": 1}) == {"a": 1}


def test_load_reads_good_file():
    p = _tmp()
    jsonstore.save(p, [{"q": "hi"}])
    assert jsonstore.load(p, []) == [{"q": "hi"}]


def test_load_corrupt_returns_default_without_raising():
    """Serving must survive a broken file -- a corrupt FAQ store should not
    take chat down with it."""
    p = _tmp()
    with open(p, "w", encoding="utf-8") as fh:
        fh.write('[{"q": "truncated"')
    assert jsonstore.load(p, []) == []


# ── save: the guard ─────────────────────────────────────────────────────

def test_save_refuses_to_overwrite_corrupt_file():
    """The heart of it. A default must never be written over a file we
    could not read."""
    p = _tmp()
    original = '[{"q": "important", "a": "truncated'
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(original)

    raised = False
    try:
        jsonstore.save(p, [])          # what the broken chain would write
    except jsonstore.StateUnreadable:
        raised = True
    assert raised, "save() overwrote an unreadable file"

    # and the original bytes are still there to be repaired
    with open(p, encoding="utf-8") as fh:
        assert fh.read() == original


def test_save_quarantines_a_copy():
    p = _tmp()
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("not json at all")
    try:
        jsonstore.save(p, {"new": True})
    except jsonstore.StateUnreadable:
        pass
    assert os.path.exists(p + jsonstore.CORRUPT_SUFFIX)


def test_quarantine_does_not_move_the_original():
    """Copy, not move: if the original were moved aside, the very next write
    would find no file, succeed, and complete the data loss."""
    p = _tmp()
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("bad")
    for _ in range(2):
        try:
            jsonstore.save(p, [])
        except jsonstore.StateUnreadable:
            pass
    assert os.path.exists(p), "the unreadable original was removed"
    # still refusing on the second attempt, not silently succeeding
    with open(p, encoding="utf-8") as fh:
        assert fh.read() == "bad"


def test_save_leaves_no_tmp_behind():
    p = _tmp()
    jsonstore.save(p, {"ok": 1})
    assert not os.path.exists(p + ".tmp")


def test_save_overwrites_a_readable_file_normally():
    """The guard must not make ordinary saves fail."""
    p = _tmp()
    jsonstore.save(p, [1, 2, 3])
    jsonstore.save(p, [4])
    assert jsonstore.load(p, []) == [4]


# ── the exact incident: UTF-8 written, platform default read ────────────

def test_non_ascii_survives_a_write_read_round_trip():
    """The 2026-09-01 failure: the store was written as real UTF-8 and read
    back under the Windows default (cp1252), which raised, was swallowed,
    and left the store reading as EMPTY. Both ends are pinned to UTF-8 now.
    """
    p = _tmp()
    entries = [{"q": "What is the operating temperature?",
                "a": "0°C to 50°C, £200 — see §2.1"}]
    jsonstore.save(p, entries)
    assert jsonstore.load(p, []) == entries


def test_cp1252_encoded_file_is_refused_not_clobbered():
    """A store written under cp1252 with real non-ASCII bytes is unreadable
    as UTF-8. That must not licence overwriting it.

    ensure_ascii=False is the load-bearing part here, and it is why the
    original bug hid for years: json.dump defaults to ensure_ascii=True,
    which escapes non-ASCII into an ASCII-only file that decodes fine under
    any of these encodings. The store only became undecodable once something
    wrote it with ensure_ascii=False -- which is exactly what happened.
    """
    p = _tmp()
    with open(p, "w", encoding="cp1252") as fh:
        json.dump([{"a": "0°C to 50°C"}], fh, ensure_ascii=False)
    assert jsonstore.load(p, []) == []      # degrades for serving
    raised = False
    try:
        jsonstore.save(p, [])
    except jsonstore.StateUnreadable:
        raised = True
    assert raised, "a cp1252 store was overwritten instead of preserved"


# ── the chain, end to end, through a real store ─────────────────────────

def test_faq_store_chain_is_broken():
    """Corrupt the FAQ store, then do exactly what the old code did:
    _load() (gets []), then _save(). The 344 real entries must survive."""
    import faq_store

    p = _tmp("faq_store.json")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write('[{"question": "real entry", "answer": "keep me"}')  # truncated

    before = open(p, encoding="utf-8").read()
    old_path = faq_store._PATH
    faq_store._PATH = p
    try:
        items = faq_store._load()
        assert items == [], "a corrupt store should read as empty"
        items.append({"question": "new", "answer": "new"})
        raised = False
        try:
            faq_store._save(items)
        except jsonstore.StateUnreadable:
            raised = True
        assert raised, "the FAQ store overwrote an unreadable file"
    finally:
        faq_store._PATH = old_path

    assert open(p, encoding="utf-8").read() == before


def test_accounts_chain_is_broken():
    """The worst case: a bad read returns [], and saving it back deletes
    every account but the one just added. These are scrypt hashes."""
    import accounts

    p = _tmp("accounts.json")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write('{"users": [{"email": "a@b.c", "hash": "trunc')

    before = open(p, encoding="utf-8").read()
    old_path = accounts._PATH
    accounts._PATH = p
    try:
        users = accounts._load()
        assert users == []
        raised = False
        try:
            accounts._save(users + [{"email": "new@x.y"}])
        except jsonstore.StateUnreadable:
            raised = True
        assert raised, "accounts overwrote an unreadable file"
    finally:
        accounts._PATH = old_path

    assert open(p, encoding="utf-8").read() == before
