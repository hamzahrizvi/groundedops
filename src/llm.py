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