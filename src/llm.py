import requests
import os

OLLAMA_URL = "http://localhost:11434/api/generate"


# --- LOCAL MODELS (OLLAMA) ---
def generate_local(prompt, model="mistral"):
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "prompt": prompt,
            "stream": False
        }
    )

    if response.status_code != 200:
        return {
            "text": f"LLM error: {response.status_code}",
            "model": model,
            "provider": "local"
        }

    data = response.json()

    return {
        "text": data.get("response", "No response from model"),
        "model": model,
        "provider": "local"
    }


# --- DEEPSEEK (OPTIONAL) ---
def generate_deepseek(prompt):
    api_key = os.getenv("DEEPSEEK_API_KEY")

    if not api_key:
        raise Exception("DeepSeek API key not set")

    response = requests.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}]
        }
    )

    if response.status_code != 200:
        raise Exception(f"DeepSeek error: {response.status_code}")

    data = response.json()

    return {
        "text": data["choices"][0]["message"]["content"],
        "model": "deepseek-chat",
        "provider": "deepseek"
    }


# --- UNIFIED GENERATE ---
def generate(provider, prompt, model="mistral"):

    if provider == "local":
        return generate_local(prompt, model)

    if provider == "deepseek":
        return generate_deepseek(prompt)

    if provider == "mock":
        return {
            "text": "Mock response",
            "model": "mock",
            "provider": "mock"
        }

    return {
        "text": "Provider not supported",
        "model": None,
        "provider": provider
    }