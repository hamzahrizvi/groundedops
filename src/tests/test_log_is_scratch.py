"""The unit suite must not append to the live src/logs.jsonl (run_tests.py
points LOG_FILE_PATH at a scratch file). Plain test_* functions, no fixtures."""
import os
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)

import logger  # noqa: E402

LIVE = os.path.abspath(os.path.join(SRC, "logs.jsonl"))


def _size(path):
    return os.path.getsize(path) if os.path.exists(path) else None


def test_unit_suite_does_not_log_to_the_live_file():
    assert os.path.abspath(logger.LOG_FILE) != LIVE, logger.LOG_FILE


def test_a_logged_turn_leaves_the_live_log_untouched():
    live_before, scratch_before = _size(LIVE), _size(logger.LOG_FILE)
    logger.log_interaction("scratch probe", "ok")
    assert _size(LIVE) == live_before
    assert (_size(logger.LOG_FILE) or 0) > (scratch_before or 0)
