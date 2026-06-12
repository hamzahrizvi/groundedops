import logging
import requests
import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_API_KEY = os.getenv("sk-74ccf52143f04a7fbbde10a45aef882a")

# Order matters — first success wins
FALLBACK_CHAIN: dict[str, list[tuple[str, str]]] = {
    "extract":   [("local", "mistral")],
    "fast":      [("local", "phi"), ("local", "mistral")],
    "accurate":  [("local", "mistral"), ("deepseek", "deepseek-chat")],
    "reasoning": [("local", "mistral"), ("deepseek", "deepseek-chat")],
}

def _call_ollama(model: str, prompt: str, timeout: int = 2000) -> dict | None:
    try:
        res = requests.post(
            OLLAMA_URL,
            json={
                "model":  model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0, "num_predict": 400},
                "keep_alive": "10m"
            },
            timeout=timeout,
        )
        res.raise_for_status()
        text = res.json().get("response", "").strip()
        if not text:
            return None
        return {"text": text, "model": model, "provider": "local"}
    except Exception as e:
        logger.warning(f"Ollama failed ({model}): {e}")
        return None

def _call_deepseek(prompt: str, model: str = "deepseek-chat", timeout: int = 300) -> dict | None:
    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY not set — skipping DeepSeek")
        return None
    try:
        res = requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json={
                "model":    model,
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
            return None
        return {"text": text, "model": model, "provider": "deepseek"}
    except Exception as e:
        logger.warning(f"DeepSeek failed: {e}")
        return None

def generate(provider: str, prompt: str, model: str) -> dict | None:
    """Single-provider call — used for explicit escalation in main.py."""
    if provider == "local":
        return _call_ollama(model, prompt)
    if provider == "deepseek":
        return _call_deepseek(prompt, model)
    logger.warning(f"Unknown provider: {provider}")
    return None

def generate_with_fallback(role: str, prompt: str) -> dict:
    """Try each (provider, model) in FALLBACK_CHAIN until one responds."""
    chain = FALLBACK_CHAIN.get(role, [("local", "mistral")])

    for i, (provider, model) in enumerate(chain):
        logger.info(f"Attempt {i+1}: {provider}/{model}")
        result = generate(provider, prompt, model)
        if result and result.get("text"):
            result["fallback_used"] = i > 0
            return result
        logger.warning(f"[{role}] {provider}/{model} failed, trying next")

    # HARD fallback: try mistral one more time with extended timeout
    logger.error(f"All fallbacks failed for role {role}, forcing mistral")
    forced = _call_ollama("mistral", prompt, timeout=300)
    if forced and forced.get("text"):
        forced["fallback_used"] = True
        return forced

    return {
        "text": "I was unable to generate a response.",
        "model": "none",
        "provider": "none",
        "fallback_used": True,
    }