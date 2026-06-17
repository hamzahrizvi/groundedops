from text_utils import looks_like_followup

MAX_MEMORY = 6
MAX_ANSWER_LEN = 300

MEMORY: list[dict] = []


def add_to_memory(query: str, answer: str) -> None:
    lower = answer.lower()
    if "could not find" in lower or "unable to generate" in lower:
        return

    MEMORY.append({
        "q": query,
        "a": answer[:MAX_ANSWER_LEN],
    })

    if len(MEMORY) > MAX_MEMORY:
        MEMORY.pop(0)


def get_memory_context() -> str:
    if not MEMORY:
        return ""

    return "\n".join(
        f"User: {m['q']}\nAssistant: {m['a']}"
        for m in MEMORY
    )


def get_last_query() -> str | None:
    """Most recent user query, used to enrich follow-up retrieval queries."""
    return MEMORY[-1]["q"] if MEMORY else None


def should_use_memory(query: str) -> bool:
    return looks_like_followup(query)


def clear_memory() -> None:
    MEMORY.clear()