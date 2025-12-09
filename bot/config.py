import os
from typing import List

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_URL = os.getenv("API_URL", "http://api:8000")
ALLOWED_USERS: List[int] = [
    int(x) for x in os.getenv("ALLOWED_USERS", "6238100241").split(",")
]
HIDE_SMALL_BALANCE_THRESHOLD = float(os.getenv("HIDE_SMALL_THRESHOLD", "1.0"))

# Notification settings
NOTIFICATION_INTERVAL = int(os.getenv("NOTIFICATION_INTERVAL", "60"))  # seconds
MIN_STABLECOIN_CHANGE = float(
    os.getenv("MIN_STABLECOIN_CHANGE", "1.0")
)  # minimum $1 for stablecoins
MIN_TOKEN_CHANGE_PERCENT = float(
    os.getenv("MIN_TOKEN_CHANGE_PERCENT", "0.1")
)  # 0.1% for other tokens
