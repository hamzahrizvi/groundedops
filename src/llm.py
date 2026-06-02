import requests

OLLAMA_URL = "http://localhost:114343/api/generate"

def ask_ollama(prompt:str): 
    response = requests.post(
        OLLAMA_URL,
        json = {
            "model" : "mistral",
            "prompt" : prompt,
            "stream" : False
        }
    )

    return response.json()["response"]

#multi model support

def generate (provider, prompt):

    if provider == "ollama":
        return ask_ollama(prompt)
    
    elif provider == "mock":
        return "Mock Response"
    
    return "Provider not supported"