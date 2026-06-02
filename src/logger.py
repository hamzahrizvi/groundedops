import json
import os

LOG_FILE = "logs.json"

def log(query, response):
    entry = {"query":query, "response": response}

    if os.path.exists(LOG_FILE):
        with open (LOG_FILE, "r") as f:
            data = json.load(f)
        
    else: data = []
        
    data.append(entry)

    with open(LOG_FILE, "w") as f:
            json.dump(data, f, indent=2)