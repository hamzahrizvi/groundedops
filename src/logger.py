import json
import os
from datetime import datetime

LOG_FILE = "logs.json"


def log_interaction(query, answer, role=None, model=None, sources=None):
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "query": query,
        "answer": answer,
        "role": role,
        "model": model,
        "sources": sources or []
    }

    # load existing logs
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except:
            logs = []
    else:
        logs = []

    logs.append(entry)

    # save back
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=2)


def get_last_logs(n=5):
    if not os.path.exists(LOG_FILE):
        return []

    with open(LOG_FILE, "r", encoding="utf-8") as f:
        logs = json.load(f)

    return logs[-n:]