import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.db import connect, is_postgres

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

def initialize_auth_table(connection) -> None:
    if is_postgres(connection):
        return
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_credentials (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()

def valid_credentials(username: str, password: str, settings: Settings, database: Path | None = None) -> bool:
    if not hmac.compare_digest(username, settings.admin_username):
        return False
    if settings.database_url or (database is not None and database.exists()):
        try:
            with connect(database) as conn:
                initialize_auth_table(conn)
                row = conn.execute("SELECT password_hash, salt FROM admin_credentials WHERE username = ?", (username,)).fetchone()
                if row:
                    expected_hash = row["password_hash"]
                    salt = row["salt"]
                    computed_hash = hash_password(password, salt)
                    return hmac.compare_digest(computed_hash, expected_hash)
        except Exception:
            pass
    return hmac.compare_digest(password, settings.admin_password)

def update_admin_password(database: Path, username: str, current_password: str, new_password: str, settings: Settings) -> None:
    if not valid_credentials(username, current_password, settings, database):
        raise ValueError("Current password is incorrect.")
    if len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters long.")
    if settings.environment == "production" and len(new_password) < 16:
        raise ValueError("Production requires a new admin password of at least 16 characters.")
    if current_password == new_password:
        raise ValueError("New password must be different from current password.")

    salt = secrets.token_hex(16)
    hashed = hash_password(new_password, salt)
    now = datetime.now(UTC).isoformat()
    with connect(database) as conn:
        initialize_auth_table(conn)
        conn.execute(
            """
            INSERT INTO admin_credentials (username, password_hash, salt, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                password_hash = excluded.password_hash,
                salt = excluded.salt,
                updated_at = excluded.updated_at
            """,
            (username, hashed, salt, now),
        )
