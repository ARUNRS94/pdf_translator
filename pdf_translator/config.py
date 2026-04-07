import os
from dotenv import load_dotenv

load_dotenv()

endpoint = os.getenv("endpoint")
api_key = os.getenv("api_key_ramesh")

HEADERS = {
    "Content-Type": "application/json"
}

MODEL = "gpt-4o"
MAX_CHARS = 3500
DEFAULT_TIMEOUT = 45
MAX_RETRIES = 3
BACKOFF_SECONDS = 2
