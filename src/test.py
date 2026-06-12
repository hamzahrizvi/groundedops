import requests, time

start = time.time()

res = requests.post(
    "http://localhost:11434/api/generate",
    json={
        "model": "phi",
        "prompt": "what is a database",
        "stream": False
    }
)

print("Time:", time.time() - start)
print(res.json()["response"])