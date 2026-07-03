MAX_MEMORY     = 5
MAX_ANSWER_LEN = 300    # Truncate long answers to avoid polluting context

MEMORY: list[dict] = []


def add_to_memory(query: str, answer: str) -> None:
    # Don't store "not found" or failure messages — they add noise
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


def clear_memory() -> None:
    MEMORY.clear()