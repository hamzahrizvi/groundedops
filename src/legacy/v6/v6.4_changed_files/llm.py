import logging
import os
import threading
import requests

from text_utils import truncate_after_refusal, build_condense_prompt, parse_condense_output, has_reference_markers

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

MODEL_LOCKS = {
    "phi": threading.Lock(),
    "mistral": threading.Lock(),
}

FALLBACK_CHAIN: dict[str, list[tuple[str, str]]] = {
    "extract":   [("local", "mistral")],
    "fast":      [("local", "phi"), ("local", "mistral")],
    "accurate":  [("local", "mistral"), ("deepseek", "deepseek-chat")],
    "reasoning": [("local", "mistral"), ("deepseek", "deepseek-chat")],
}

# Models offered for the manual "rethink with a different model" feature.
RETHINK_OPTIONS: list[tuple[str, str]] = [
    ("local", "phi"),
    ("local", "mistral"),
    ("deepseek", "deepseek-chat"),
]

# Model used for query condensation — phi, since this is a short,
# latency-sensitive auxiliary call on every turn beyond the first.
CONDENSE_MODEL = "phi"


def _call_ollama(
    model: str,
    prompt: str,
    timeout: int | None = None,
    num_predict: int | None = None,
    keep_alive: str = "10m",
) -> dict | None:
    if timeout is None:
        timeout = 40 if model == "phi" else 90
    if num_predict is None:
        num_predict = 120 if model == "phi" else 160

    lock = MODEL_LOCKS.get(model)

    try:
        if lock:
            lock.acquire()

        res = requests.post(
            OLLAMA_URL,
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0,
                    "num_predict": num_predict,
                    "stop": ["\n\nQuestion:", "\n\nContext:", "<context>"],
                },
                "keep_alive": keep_alive,
            },
            timeout=timeout,
        )

        res.raise_for_status()
        text = res.json().get("response", "").strip()

        if not text:
            logger.warning(f"Ollama empty response ({model})")
            return None

        text = truncate_after_refusal(text)

        return {"text": text, "model": model, "provider": "local"}

    except Exception as e:
        logger.warning(f"Ollama failed ({model}): {e}")
        return None

    finally:
        if lock:
            lock.release()


def _call_deepseek(
    prompt: str,
    model: str = "deepseek-chat",
    timeout: int = 60,
    api_key: str | None = None,
) -> dict | None:
    key = api_key or os.getenv("DEEPSEEK_API_KEY")
    if not key:
        logger.info("No DeepSeek key — skipping")
        return None

    try:
        res = requests.post(
            DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
            timeout=timeout,
        )

        if res.status_code != 200:
            logger.warning(f"DeepSeek HTTP {res.status_code}")
            return None

        text = res.json()["choices"][0]["message"]["content"].strip()
        if not text:
            logger.warning("DeepSeek empty response")
            return None

        text = truncate_after_refusal(text)
        return {"text": text, "model": model, "provider": "deepseek"}

    except Exception as e:
        logger.warning(f"DeepSeek failed ({model}): {e}")
        return None


def generate(
    provider: str,
    prompt: str,
    model: str,
    deepseek_api_key: str | None = None,
) -> dict | None:
    if provider == "local":
        return _call_ollama(model, prompt)
    if provider == "deepseek":
        return _call_deepseek(prompt, model=model, api_key=deepseek_api_key)

    logger.warning(f"Unknown provider: {provider}")
    return None


def safe_generate(
    provider: str,
    prompt: str,
    model: str,
    deepseek_api_key: str | None = None,
) -> dict | None:
    for attempt in range(2):
        result = generate(provider, prompt, model, deepseek_api_key)
        if result and result.get("text"):
            return result
        logger.warning(f"Retry {attempt + 1} failed for {provider}/{model}")
    return None


def generate_with_fallback(
    role: str,
    prompt: str,
    deepseek_api_key: str | None = None,
) -> dict:
    """
    Try each (provider, model) in FALLBACK_CHAIN[role] in order, ONE
    attempt per entry, returning the first success.

    BUG FIX: this used to call safe_generate() per chain entry, which
    internally retries the SAME model twice before returning failure —
    each retry paying the full _call_ollama timeout (90s for non-phi
    models). For role="reasoning"/"accurate" (chain = [mistral,
    deepseek]), a slow/timing-out mistral could burn up to 180s on
    mistral ALONE before the loop even reached the deepseek entry —
    exceeding typical client-side request timeouts before the actual
    fallback provider was ever attempted. Reproduced from a production
    run where query role="reasoning" timed out client-side at 180s with
    no evidence deepseek was reached.

    Each chain entry now gets exactly one attempt via generate() (no
    internal retry), so the chain advances to the next provider as soon
    as the current one fails — matching the actual intent of
    FALLBACK_CHAIN. The single extra "forced mistral" safety net at the
    end (for chains that don't already include local/mistral) is
    unchanged and still gets a fresh single attempt.
    """
    chain = FALLBACK_CHAIN.get(role, [("local", "mistral")])
    tried = set()

    for i, (provider, model) in enumerate(chain):
        tried.add((provider, model))
        logger.info(f"Attempt {i+1}: {provider}/{model}")

        result = generate(provider, prompt, model, deepseek_api_key)

        if result and result.get("text"):
            result["fallback_used"] = i > 0
            return result

        logger.warning(f"[{role}] {provider}/{model} failed")

    if ("local", "mistral") not in tried:
        logger.warning("Forcing mistral final attempt")
        forced = generate("local", prompt, "mistral", deepseek_api_key)
        if forced and forced.get("text"):
            forced["fallback_used"] = True
            return forced

    logger.error(f"All fallbacks failed for role {role}")
    return {
        "text": "I was unable to generate a response.",
        "model": "none",
        "provider": "none",
        "fallback_used": True,
    }


def condense_query(current_query: str, history: list[dict], model: str = CONDENSE_MODEL) -> str:
    """
    Rewrite-Retrieve-Read style query condensation. If `current_query`
    depends on prior conversation turns, this resolves it into a
    standalone query using a fast local model. If the query is already
    self-contained, it is returned unchanged with no model call.

    TWO GUARDS before calling the model:
      1. No history — nothing to resolve against, skip immediately.
      2. No reference markers — the query is clearly self-contained
         (checked via text_utils.has_reference_markers). This prevents
         phi from incorrectly rewriting standalone queries like "post
         installation verification installer sign off" into whatever
         topic happened to appear in the previous turn.

    Falls back to `current_query` unchanged on any model failure.
    """
    if not history:
        return current_query

    if not has_reference_markers(current_query):
        return current_query

    prompt = build_condense_prompt(current_query, history)
    result = _call_ollama(model, prompt, timeout=20, num_predict=64)

    if not result or not result.get("text"):
        logger.warning("Query condensation failed — using original query unchanged")
        return current_query

    return parse_condense_output(result["text"], fallback_query=current_query)


def warmup_local_models(models: list[str] | None = None) -> dict[str, bool]:
    models = models or ["phi", "mistral"]
    results: dict[str, bool] = {}

    for model in models:
        logger.info(f"Warming model: {model}")
        result = _call_ollama(
            model=model,
            prompt="ping",
            timeout=120,
            num_predict=8,
            keep_alive="10m",
        )

        ok = bool(result and result.get("text"))
        results[model] = ok

        if ok:
            logger.info(f"Warmup ok: {model}")
        else:
            logger.warning(f"Warmup failed: {model}")

    return results
