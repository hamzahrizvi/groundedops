"""Server-side conversation storage (v2.1).

Registered users resume past chats across devices/sessions; anonymous
users keep the existing browser-local (ephemeral) path and never touch
this store.

STORAGE: SQLite by default (zero infra, works today). Set DATABASE_URL
to a Postgres DSN for production — the schema is standard SQL; swap the
connection layer in _connect() when you move to Postgres.

⚠ IDENTITY SEAM — READ BEFORE PRODUCTION ⚠
resolve_user_id() is where the website's verified identity MUST plug in.
Right now it trusts an X-User-Id header, which is NOT SECURE — anyone
could impersonate any user by setting it. This is deliberate scaffolding
so the storage + UI can be built and tested now. BEFORE this faces real
customers, replace the body of resolve_user_id() with verification of
the signed token issued by innovative-technology.com's auth system
(verify signature -> extract user id). Until then, do not deploy this
to a public endpoint. The security integration is tracked separately.
"""
import os
import sqlite3
import time
import uuid
import json
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("CONVO_DB_PATH", "conversations.db")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id         TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL,
            product    TEXT,
            title      TEXT,
            created    REAL NOT NULL,
            updated    REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_convo_user ON conversations(user_id, updated DESC);
        CREATE TABLE IF NOT EXISTS messages (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL,
            sources         TEXT,
            created         REAL NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_msg_convo ON messages(conversation_id, created);
        """)
    logger.info(f"Conversation store ready at {DB_PATH}")


def resolve_user_id(x_user_id: str | None) -> str | None:
    """⚠ INSECURE PLACEHOLDER — see module docstring.
    Returns a stable user id for a registered user, or None for anonymous.
    REPLACE with signed-token verification before production."""
    if not x_user_id:
        return None
    return x_user_id.strip() or None


def list_conversations(user_id: str) -> list[dict]:
    with _connect() as c:
        rows = c.execute(
            "SELECT id, product, title, created, updated FROM conversations "
            "WHERE user_id = ? ORDER BY updated DESC", (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(user_id: str, convo_id: str) -> dict | None:
    with _connect() as c:
        convo = c.execute(
            "SELECT id, product, title, created, updated FROM conversations "
            "WHERE id = ? AND user_id = ?", (convo_id, user_id)
        ).fetchone()
        if not convo:
            return None  # not found OR not owned by this user — same response, no leak
        msgs = c.execute(
            "SELECT role, content, sources, created FROM messages "
            "WHERE conversation_id = ? ORDER BY created", (convo_id,)
        ).fetchall()
    out = dict(convo)
    out["messages"] = [
        {**dict(m), "sources": json.loads(m["sources"]) if m["sources"] else None}
        for m in msgs
    ]
    return out


def save_turn(user_id: str, convo_id: str | None, product: str | None,
              user_msg: str, assistant_msg: str, sources: list | None) -> str:
    """Append a user+assistant turn, creating the conversation if needed.
    Returns the conversation id."""
    now = time.time()
    with _connect() as c:
        if not convo_id or not c.execute(
                "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?",
                (convo_id, user_id)).fetchone():
            convo_id = convo_id or str(uuid.uuid4())
            title = (user_msg or "Chat")[:60]
            c.execute("INSERT OR IGNORE INTO conversations "
                      "(id, user_id, product, title, created, updated) "
                      "VALUES (?,?,?,?,?,?)",
                      (convo_id, user_id, product, title, now, now))
        for role, content in (("user", user_msg), ("assistant", assistant_msg)):
            c.execute("INSERT INTO messages (id, conversation_id, role, content, sources, created) "
                      "VALUES (?,?,?,?,?,?)",
                      (str(uuid.uuid4()), convo_id, role, content,
                       json.dumps(sources) if role == "assistant" and sources else None, now))
        c.execute("UPDATE conversations SET updated = ? WHERE id = ?", (now, convo_id))
    return convo_id


def delete_conversation(user_id: str, convo_id: str) -> bool:
    with _connect() as c:
        cur = c.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?",
                        (convo_id, user_id))
        c.execute("DELETE FROM messages WHERE conversation_id = ?", (convo_id,))
        return cur.rowcount > 0
