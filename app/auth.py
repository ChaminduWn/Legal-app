import hashlib
import hmac
import time

from app.config import Settings

COOKIE_NAME = "neethi_admin_session"

def create_session(username: str, settings: Settings) -> str:
    expires = int(time.time()) + settings.admin_session_hours * 3600
    payload = f"{username}|{expires}"
    signature = hmac.new(settings.admin_session_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{signature}"

def valid_session(token: str | None, settings: Settings) -> bool:
    if not token:
        return False
    try:
        username, expires, signature = token.rsplit("|", 2)
        payload = f"{username}|{expires}"
        expected = hmac.new(settings.admin_session_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected) and hmac.compare_digest(username, settings.admin_username) and int(expires) > int(time.time())
    except (ValueError, TypeError):
        return False

def valid_credentials(username: str, password: str, settings: Settings) -> bool:
    return hmac.compare_digest(username, settings.admin_username) and hmac.compare_digest(password, settings.admin_password)
