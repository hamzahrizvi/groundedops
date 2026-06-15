import logging
import threading
import requests

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

# Per-model locks (NOT global)
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


# ---------------------------
# OLLAMA CALL
# ---------------------------
def _call_ollama(
    model: str,
    prompt: str,
    timeout: int | None = None,
    num_predict: int | None = None,
    keep_alive: str = "10m",
) -> dict | None:

    # Keep YOUR timings exactly
    if timeout is None:
        timeout = 40 if model == "phi" else 90
    if num_predict is None:
        num_predict = 120 if model == "phi" else 200

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

        return {
            "text": text,
            "model": model,
            "provider": "local"
        }

    except Exception as e:
        logger.warning(f"Ollama failed ({model}): {e}")
        return None

    finally:
        if lock:
            lock.release()


# ---------------------------
# DEEPSEEK CALL
# ---------------------------
def _call_deepseek(
    prompt: str,
    model: str = "deepseek-chat",
    timeout: int = 60,
    api_key: str | None = None,
) -> dict | None:

    if not api_key:
        logger.info("No DeepSeek key — skipping")
        return None

    try:
        res = requests.post(
            DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
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

        return {
            "text": text,
            "model": model,
            "provider": "deepseek"
        }

    except Exception as e:
        logger.warning(f"DeepSeek failed ({model}): {e}")
        return None


# ---------------------------
# SINGLE GENERATE
# ---------------------------
def generate(
    provider: str,
    prompt: str,
    model: str,
    deepseek_api_key: str | None = None
) -> dict | None:

    if provider == "local":
        return _call_ollama(model, prompt)

    if provider == "deepseek":
        return _call_deepseek(prompt, model=model, api_key=deepseek_api_key)

    logger.warning(f"Unknown provider: {provider}")
    return None


# ---------------------------
# SAFE GENERATE (RETRY)
# ---------------------------
def safe_generate(
    provider: str,
    prompt: str,
    model: str,
    deepseek_api_key: str | None = None
) -> dict | None:

    for attempt in range(2):  # lightweight retry
        result = generate(provider, prompt, model, deepseek_api_key)
        if result and result.get("text"):
            return result
        logger.warning(f"Retry {attempt+1} failed for {provider}/{model}")

    return None


# ---------------------------
# FALLBACK CHAIN
# ---------------------------
def generate_with_fallback(
    role: str,
    prompt: str,
    deepseek_api_key: str | None = None
) -> dict:

    chain = FALLBACK_CHAIN.get(role, [("local", "mistral")])
    tried = set()

    for i, (provider, model) in enumerate(chain):
        tried.add((provider, model))

        logger.info(f"Attempt {i+1}: {provider}/{model}")

        result = safe_generate(provider, prompt, model, deepseek_api_key)

        if result:
            result["fallback_used"] = i > 0
            return result

        logger.warning(f"[{role}] {provider}/{model} failed")

    # Only force mistral if NOT already tried
    if ("local", "mistral") not in tried:
        logger.warning("Forcing mistral final attempt")

        forced = safe_generate("local", prompt, "mistral", deepseek_api_key)

        if forced:
            forced["fallback_used"] = True
            return forced

    logger.error(f"All fallbacks failed for role {role}")

    return {
        "text": "I was unable to generate a response.",
        "model": "none",
        "provider": "none",
        "fallback_used": True,
    }


# ---------------------------
# WARMUP
# ---------------------------
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