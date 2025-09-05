# config.py
import os
from dotenv import load_dotenv
load_dotenv()

API_BASE = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")
TOKEN_URL = f"{API_BASE}/rest_api/token/"
REFRESH_URL = f"{API_BASE}/rest_api/token/refresh/"
DEFAULT_TIMEOUT = 20

# nou:
MEDIA_URL = os.getenv("MEDIA_URL", "/images/")  # trebuie să înceapă și să se termine cu slash
if not MEDIA_URL.startswith("/"):
    MEDIA_URL = "/" + MEDIA_URL
if not MEDIA_URL.endswith("/"):
    MEDIA_URL += "/"
