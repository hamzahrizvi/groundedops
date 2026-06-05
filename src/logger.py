import json
import logging
import os
from datetime import datetime

LOG_FILE = "logs.json"
_logger = logging.getLogger(__name__)


def log_interaction(
    query: str,
    answer: str,
    role: str | None = None,
    model: str | None = None,
    sources: list[str] | None = None,
    grounding_score: float | None = None,
    flagged: bool = False,
) -> None:
    entry = {
        "timestamp":      datetime.utcnow().isoformat(),
        "query":          query,
        "answer":         (answer or "")[:500],     # cap very long answers
        "role":           role,
        "model":          model,
        "sources":        sources or [],
        "grounding_score": grounding_score,
        "flagged":        flagged,
    }

    logs: list[dict] = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except (json.JSONDecodeError, IOError):
            logs = []

    logs.append(entry)

    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=2)
    except IOError as exc:
        _logger.error(f"Failed to write log: {exc}")


def get_last_logs(n: int = 5) -> list[dict]:
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
        return logs[-n:]
    except (json.JSONDecodeError, IOError):
        return []


def get_flagged_logs() -> list[dict]:
    """Return all interactions where grounding check failed."""
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
        return [entry for entry in logs if entry.get("flagged")]
    except (json.JSONDecodeError, IOError):
        return []