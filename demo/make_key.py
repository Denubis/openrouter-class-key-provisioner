import requests
import os

url = "https://openrouter.ai/api/v1/keys"

# Get env OPENROUTER_PROVISIONING_KEY
API_KEY = os.getenv("OPENROUTER_PROVISIONING_KEY")
payload = { "name": "string" }
headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

response = requests.post(url, json=payload, headers=headers)

print(response.json())