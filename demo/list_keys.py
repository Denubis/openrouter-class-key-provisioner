import requests
from rich import print
import os

url = "https://openrouter.ai/api/v1/keys"
API_KEY = os.getenv("OPENROUTER_PROVISIONING_KEY")

headers = {"Authorization": f"Bearer {API_KEY}"}

response = requests.get(url, headers=headers)

print(response.json())