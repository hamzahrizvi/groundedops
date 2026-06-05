import logging
import requests
import os

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")


# order matters here — first success wins
FALLBACK_CHAIN = {
    "extract":   [("local", "mistral")],
    "fast":      [("local", "phi"), ("local", "mistral")],
    "accurate":  [("local", "mistral"), ("deepseek", "deepseek-chat")], #if mistral fails to answer in x secs will revert to deepseek basically
    "reasoning": [("local", "mistral"), ("deepseek", "deepseek-chat")],
}


def _call_ollama(model: str, prompt: str, timeout=60):
    try:
        res = requests.post(
            OLLAMA_URL,
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0,
                    "num_predict": 400,  # keep this reasonable
                },
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


def _call_deepseek(prompt: str, model="deepseek-chat", timeout=30):
    if not DEEPSEEK_API_KEY:
        return None

    try:
        res = requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
            timeout=timeout,
        )

        if res.status_code != 200:
            return None

        text = res.json()["choices"][0]["message"]["content"].strip()
        return {"text": text, "model": model, "provider": "deepseek"}

    except Exception:
        return None


def generate_with_fallback(role: str, prompt: str):
    chain = FALLBACK_CHAIN.get(role, [("local", "mistral")])

    for i, (provider, model) in enumerate(chain):
        if provider == "local":
            result = _call_ollama(model, prompt)
        else:
            result = _call_deepseek(prompt, model)

        if result:
            result["fallback_used"] = i > 0
            return result

    return {
        "text": "I was unable to generate a response.",
        "model": "none",
        "provider": "none",
        "fallback_used": True,
    }