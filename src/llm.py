import requests

OLLAMA_URL = "http://localhost:11434/api/generate"

def ask_ollama(prompt):
    response = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": "mistral",
            "prompt": prompt,
            "stream": False
        }
    )

    if response.status_code != 200:
        return f"LLM error: {response.status_code}"

    data = response.json()
    return data.get("response", "No response from model")

#multi model support

def generate (provider, prompt):

    if provider == "ollama":
        return ask_ollama(prompt)
    
    elif provider == "mock":
        return "Mock Response"
    
    return "Provider not supported"