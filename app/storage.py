"""Immutable originals on a configurable durable volume, outside static assets."""
import hashlib
import mimetypes
import os
import shutil
import tempfile
from pathlib import Path

from app.rag.store import connect


def store_file(root: Path, path: Path) -> dict:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    suffix = path.suffix.lower()
    if suffix not in {".pdf", ".jpg", ".jpeg", ".png", ".webp"}:
        raise ValueError("Unsupported original file type.")
    key = f"{digest[:2]}/{digest}{suffix}"
    target = root / key
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".incoming-")
        try:
            with os.fdopen(handle, "wb") as stream, path.open("rb") as source:
                shutil.copyfileobj(source, stream)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return dict(storage_key=key, sha256=digest, byte_size=path.stat().st_size,
                media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                original_name=path.name)


def register_asset(connection, source_file: str, page: int, asset: dict):
    connection.execute("""INSERT INTO document_assets
        (source_file,page_number,storage_key,sha256,byte_size,media_type,original_name)
        VALUES (?,?,?,?,?,?,?) ON CONFLICT(source_file,page_number) DO NOTHING""",
        (source_file,page,asset['storage_key'],asset['sha256'],asset['byte_size'],asset['media_type'],asset['original_name']))


def resolve_asset(database: Path, root: Path, source_file: str, page: int = 0) -> Path | None:
    with connect(database) as connection:
        row = connection.execute("SELECT storage_key FROM document_assets WHERE source_file=? AND page_number=?",
                                 (source_file,page)).fetchone()
    if not row:
        return None
    candidate = (root / row[0]).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        return None
    return candidate
