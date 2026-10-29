"""
migrate_to_supabase_v2.py
Full migration of the upgraded legal_index.sqlite3 database to Supabase (PostgreSQL).

Tables migrated:
  - document_metadata         (core catalog)
  - document_records          (title lookup)
  - legal_instruments         (instrument registry)
  - document_versions         (version catalog)
  - version_relationships     (version links)
  - document_assets           (page asset manifest)
  - legal_topics              (topic taxonomy)
  - document_topics           (document <-> topic)
  - legal_chunks              (text chunks)
  - legal_embeddings          (vector embeddings)
  - section_reviews           (review workflow)
  - legal_relationships       (cross-doc relationships)
  - audit_log                 (admin audit trail)
  - archived_metadata         (archived records)
  - collection_files          (collection ingest state)
  - admin_credentials         (hashed admin logins)
  - site_content              (CMS content blobs)

Usage:
  python scripts/migrate_to_supabase_v2.py

Requires DATABASE_URL in .env or environment.
"""

import os
import sys
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np

try:
    import psycopg
    from pgvector.psycopg import register_vector
except ImportError:
    print("[!] Missing dependencies. Run:")
    print('    pip install "psycopg[binary]>=3.1" pgvector')
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_PATH = Path(os.environ.get("LEGAL_AI_DATABASE", "D:/legal-app-data/legal_index.sqlite3"))
BACKUP_DIR = Path("data/backups")
BATCH_SIZE = 500  # rows per executemany batch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_database_url() -> str:
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        env_file = Path(".env")
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    db_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not db_url:
        print("[!] DATABASE_URL not found in environment or .env file.")
        sys.exit(1)
    return db_url


def backup_sqlite(path: Path) -> None:
    if not path.exists():
        print(f"[-] SQLite file not found: {path}")
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"legal_index_v2_backup_{ts}.sqlite3"
    shutil.copy2(path, dest)
    print(f"[+] Backup created: {dest}")


def connect_pg(database_url: str):
    """Connect to Postgres, with Supabase IPv4 pooler fallback."""
    import urllib.parse
    try:
        return psycopg.connect(database_url, autocommit=False, connect_timeout=15)
    except Exception as direct_err:
        print(f"[-] Direct connection failed: {direct_err}")
        try:
            parsed = urllib.parse.urlparse(database_url)
            host = parsed.hostname or ""
            if host.startswith("db.") and host.endswith(".supabase.co"):
                project_ref = host.split(".")[1]
                password = urllib.parse.unquote(parsed.password or "")
                db = parsed.path.lstrip("/") or "postgres"
                pooler_host = "aws-0-ap-southeast-1.pooler.supabase.com"
                pooler_user = f"postgres.{project_ref}"
                print(f"[*] Falling back to IPv4 pooler ({pooler_host})...")
                return psycopg.connect(
                    host=pooler_host, port=5432,
                    user=pooler_user, password=password, dbname=db,
                    autocommit=False, connect_timeout=15,
                )
        except Exception as pooler_err:
            print(f"[-] Pooler fallback failed: {pooler_err}")
        raise direct_err


def batched(rows, size):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def migrate_table(cur, name, rows, sql, mapper):
    """Insert rows in batches using executemany."""
    if not rows:
        print(f"    (no rows to migrate)")
        return
    total = 0
    for batch in batched(rows, BATCH_SIZE):
        cur.executemany(sql, [mapper(r) for r in batch])
        total += len(batch)
    print(f"    migrated {total} rows")


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

DDL = """
-- Extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- document_metadata
CREATE TABLE IF NOT EXISTS document_metadata (
    source_file            TEXT PRIMARY KEY,
    official_source_url    TEXT,
    publication_date       TEXT,
    effective_date         TEXT,
    status                 TEXT NOT NULL DEFAULT 'unreviewed',
    notes                  TEXT,
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    document_type          TEXT NOT NULL DEFAULT 'other',
    extraction_method      TEXT NOT NULL DEFAULT 'text',
    extraction_confidence  REAL,
    extraction_warnings    JSONB NOT NULL DEFAULT '[]'::jsonb
);

-- document_records (title lookup)
CREATE TABLE IF NOT EXISTS document_records (
    source_file  TEXT PRIMARY KEY,
    title        TEXT NOT NULL
);

-- legal_instruments
CREATE TABLE IF NOT EXISTS legal_instruments (
    instrument_id  TEXT PRIMARY KEY,
    title          TEXT NOT NULL
);

-- document_versions
CREATE TABLE IF NOT EXISTS document_versions (
    source_file       TEXT PRIMARY KEY,
    instrument_id     TEXT NOT NULL REFERENCES legal_instruments(instrument_id),
    edition_year      INTEGER NOT NULL,
    version_kind      TEXT NOT NULL,
    valid_from        TEXT NOT NULL,
    valid_to          TEXT,
    supersedes_source TEXT
);

-- version_relationships
CREATE TABLE IF NOT EXISTS version_relationships (
    source_file          TEXT PRIMARY KEY,
    target_instrument_id TEXT NOT NULL REFERENCES legal_instruments(instrument_id),
    relation_type        TEXT NOT NULL,
    evidence             TEXT NOT NULL
);

-- document_assets
CREATE TABLE IF NOT EXISTS document_assets (
    source_file   TEXT NOT NULL,
    page_number   INTEGER NOT NULL,
    storage_key   TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    byte_size     INTEGER NOT NULL,
    media_type    TEXT NOT NULL,
    original_name TEXT NOT NULL,
    PRIMARY KEY (source_file, page_number)
);

-- legal_topics
CREATE TABLE IF NOT EXISTS legal_topics (
    topic_id   TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    parent_id  TEXT REFERENCES legal_topics(topic_id)
);

-- document_topics
CREATE TABLE IF NOT EXISTS document_topics (
    source_file  TEXT NOT NULL,
    topic_id     TEXT NOT NULL REFERENCES legal_topics(topic_id),
    PRIMARY KEY (source_file, topic_id)
);

-- legal_chunks
CREATE TABLE IF NOT EXISTS legal_chunks (
    source_id   TEXT PRIMARY KEY,
    law         TEXT NOT NULL,
    section     TEXT NOT NULL,
    heading     TEXT,
    text        TEXT NOT NULL,
    source_file TEXT NOT NULL,
    page_start  INTEGER NOT NULL,
    page_end    INTEGER NOT NULL
);

-- legal_embeddings
CREATE TABLE IF NOT EXISTS legal_embeddings (
    source_id  TEXT PRIMARY KEY,
    model      TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector     vector
);

-- section_reviews
CREATE TABLE IF NOT EXISTS section_reviews (
    source_id        TEXT PRIMARY KEY,
    original_heading TEXT,
    original_text    TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'unreviewed',
    notes            TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- legal_relationships
CREATE TABLE IF NOT EXISTS legal_relationships (
    id             BIGSERIAL PRIMARY KEY,
    source_file    TEXT NOT NULL,
    relation_type  TEXT NOT NULL,
    target_law     TEXT NOT NULL,
    target_section TEXT,
    evidence       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'proposed'
);

-- audit_log
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    source_file TEXT NOT NULL,
    action      TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'admin',
    details     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- archived_metadata
CREATE TABLE IF NOT EXISTS archived_metadata (
    source_file  TEXT PRIMARY KEY,
    record_json  TEXT NOT NULL,
    reason       TEXT NOT NULL,
    archived_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- collection_files
CREATE TABLE IF NOT EXISTS collection_files (
    origin_path      TEXT PRIMARY KEY,
    sha256           TEXT NOT NULL,
    byte_size        INTEGER NOT NULL,
    source_file      TEXT,
    storage_key      TEXT,
    state            TEXT NOT NULL,
    pages            INTEGER NOT NULL DEFAULT 0,
    extracted_pages  INTEGER NOT NULL DEFAULT 0,
    warnings_json    TEXT NOT NULL DEFAULT '[]',
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- admin_credentials
CREATE TABLE IF NOT EXISTS admin_credentials (
    username       TEXT PRIMARY KEY,
    password_hash  TEXT NOT NULL,
    salt           TEXT NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- site_content
CREATE TABLE IF NOT EXISTS site_content (
    id            TEXT PRIMARY KEY,
    content_json  TEXT NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


# ---------------------------------------------------------------------------
# Main migration
# ---------------------------------------------------------------------------

def migrate():
    db_url = load_database_url()

    print(f"\n{'='*60}")
    print("  Legal App: SQLite -> Supabase Migration (v2)")
    print(f"{'='*60}")
    print(f"[+] Source DB: {DB_PATH}")

    if not DB_PATH.exists():
        print(f"[-] SQLite database not found: {DB_PATH}")
        sys.exit(1)

    backup_sqlite(DB_PATH)

    # Open SQLite
    sqlite_conn = sqlite3.connect(DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row

    print(f"\n[+] Connecting to Supabase PostgreSQL...")
    with connect_pg(db_url) as pg:
        with pg.cursor() as cur:
            # ── Schema ──────────────────────────────────────────────────
            print("\n[*] Creating schema and extensions...")
            for stmt in DDL.split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)
            pg.commit()
            print("    Schema ready.")

        register_vector(pg)

        with pg.cursor() as cur:

            # ── 1. document_metadata ────────────────────────────────────
            print("\n[1] document_metadata")
            rows = sqlite_conn.execute("SELECT * FROM document_metadata").fetchall()
            print(f"    {len(rows)} rows found")
            migrate_table(cur, "document_metadata", rows,
                """
                INSERT INTO document_metadata (
                    source_file, official_source_url, publication_date, effective_date,
                    status, notes, updated_at, document_type, extraction_method,
                    extraction_confidence, extraction_warnings
                ) VALUES (
                    %(source_file)s, %(official_source_url)s, %(publication_date)s, %(effective_date)s,
                    %(status)s, %(notes)s,
                    COALESCE(%(updated_at)s::timestamptz, NOW()),
                    COALESCE(%(document_type)s, 'other'),
                    COALESCE(%(extraction_method)s, 'text'),
                    %(extraction_confidence)s,
                    COALESCE(%(extraction_warnings)s::jsonb, '[]'::jsonb)
                )
                ON CONFLICT (source_file) DO UPDATE SET
                    official_source_url   = EXCLUDED.official_source_url,
                    publication_date      = EXCLUDED.publication_date,
                    effective_date        = EXCLUDED.effective_date,
                    status                = EXCLUDED.status,
                    notes                 = EXCLUDED.notes,
                    updated_at            = EXCLUDED.updated_at,
                    document_type         = EXCLUDED.document_type,
                    extraction_method     = EXCLUDED.extraction_method,
                    extraction_confidence = EXCLUDED.extraction_confidence,
                    extraction_warnings   = EXCLUDED.extraction_warnings
                """,
                dict,
            )

            # ── 2. document_records ─────────────────────────────────────
            print("\n[2] document_records")
            rows = sqlite_conn.execute("SELECT * FROM document_records").fetchall()
            print(f"    {len(rows)} rows found")
            migrate_table(cur, "document_records", rows,
                """
                INSERT INTO document_records (source_file, title)
                VALUES (%(source_file)s, %(title)s)
                ON CONFLICT (source_file) DO UPDATE SET title = EXCLUDED.title
                """,
                dict,
            )

            # ── 3. legal_instruments ────────────────────────────────────
            print("\n[3] legal_instruments")
            try:
                rows = sqlite_conn.execute("SELECT * FROM legal_instruments").fetchall()
                print(f"    {len(rows)} rows found")
                migrate_table(cur, "legal_instruments", rows,
                    """
                    INSERT INTO legal_instruments (instrument_id, title)
                    VALUES (%(instrument_id)s, %(title)s)
                    ON CONFLICT (instrument_id) DO UPDATE SET title = EXCLUDED.title
                    """,
                    dict,
                )
            except sqlite3.OperationalError:
                print("    (table not present, skipped)")

            # ── 4. document_versions ────────────────────────────────────
            print("\n[4] document_versions")
            try:
                rows = sqlite_conn.execute("SELECT * FROM document_versions").fetchall()
                print(f"    {len(rows)} rows found")
                migrate_table(cur, "document_versions", rows,
                    """
                    INSERT INTO document_versions (
                        source_file, instrument_id, edition_year, version_kind,
                        valid_from, valid_to, supersedes_source
                    ) VALUES (
                        %(source_file)s, %(instrument_id)s, %(edition_year)s, %(version_kind)s,
                        %(valid_from)s, %(valid_to)s, %(supersedes_source)s
                    )
                    ON CONFLICT (source_file) DO UPDATE SET
                        instrument_id     = EXCLUDED.instrument_id,
                        edition_year      = EXCLUDED.edition_year,
                        version_kind      = EXCLUDED.version_kind,
                        valid_from        = EXCLUDED.valid_from,
                        valid_to          = EXCLUDED.valid_to,
                        supersedes_source = EXCLUDED.supersedes_source
                    """,
                    dict,
                )
            except sqlite3.OperationalError:
                print("    (table not present, skipped)")

            # ── 5. version_relationships ────────────────────────────────
            print("\n[5] version_relationships")
            try:
                rows = sqlite_conn.execute("SELECT * FROM version_relationships").fetchall()
                print(f"    {len(rows)} rows found")
                migrate_table(cur, "version_relationships", rows,
                    """
                    INSERT INTO version_relationships (
                        source_file, target_instrument_id, relation_type, evidence
                    ) VALUES (
                        %(source_file)s, %(target_instrument_id)s, %(relation_type)s, %(evidence)s
                    )
                    ON CONFLICT (source_file) DO UPDATE SET
                        target_instrument_id = EXCLUDED.target_instrument_id,
                        relation_type        = EXCLUDED.relation_type,
                        evidence             = EXCLUDED.evidence
                    """,
                    dict,
                )
            except sqlite3.OperationalError:
                print("    (table not present, skipped)")

            # ── 6. document_assets ───────────────────────────────────────
            print("\n[6] document_assets")
            try:
                rows = sqlite_conn.execute("SELECT * FROM document_assets").fetchall()
                print(f"    {len(rows)} rows found")
                migrate_table(cur, "document_assets", rows,
                    """
                    INSERT INTO document_assets (
                        source_file, page_number, storage_key, sha256,
                        byte_size, media_type, original_name
                    ) VALUES (
                        %(source_file)s, %(page_number)s, %(storage_key)s, %(sha256)s,
                        %(byte_size)s, %(media_type)s, %(original_name)s
                    )
                    ON CONFLICT (source_file, page_number) DO UPDATE SET
                        storage_key   = EXCLUDED.storage_key,
                        sha256        = EXCLUDED.sha256,
                        byte_size     = EXCLUDED.byte_size,
                        media_type    = EXCLUDED.media_type,
                        original_name = EXCLUDED.original_name
                    """,
                    dict,
                )
            except sqlite3.OperationalError:
                print("    (table not present, skipped)")

            # ── 7. legal_topics ──────────────────────────────────────────
            print("\n[7] legal_topics")
            try:
                rows = sqlite_conn.execute("SELECT * FROM legal_topics ORDER BY parent_id NULLS FIRST").fetchall()
                print(f"    {len(rows)} rows found")
                migrate_table(cur, "legal_topics", rows,
                    """
                    INSERT INTO legal_topics (topic_id, name, parent_id)
                    VALUES (%(topic_id)s, %(name)s, %(parent_id)s)
                    ON CONFLICT (topic_id) DO UPDATE SET
                        name      = EXCLUDED.name,
                        parent_id = EXCLUDED.parent_id
                    """,
                    dict,
                )
            except sqlite3.OperationalError:
                print("    (table not present, skipped)")

            # ── 8. document_topics ───────────────────────────────────────
            print("\n[8] document_topics")
            try:
                rows = sqlite_conn.execute("SELECT * FROM document_topics").fetchall()
                print(f"    {len(rows)} rows found")
                migrate_table(cur, "document_topics", rows,
                    """
                    INSERT INTO document_topics (source_file, topic_id)
                    VALUES (%(source_file)s, %(topic_id)s)
                    ON CONFLICT (source_file, topic_id) DO NOTHING
                    """,
                    dict,
                )
            except sqlite3.OperationalError:
                print("    (table not present, skipped)")

            # ── 9. legal_chunks ──────────────────────────────────────────
            print("\n[9] legal_chunks")
            rows = sqlite_conn.execute("SELECT * FROM legal_chunks").fetchall()
            print(f"    {len(rows)} rows found")
            # Ensure all referenced source_files exist in document_metadata
            unique_files = list({r["source_file"] for r in rows})
            for batch in batched(unique_files, BATCH_SIZE):
                cur.executemany(
                    "INSERT INTO document_metadata (source_file, status) VALUES (%s, 'unreviewed') ON CONFLICT (source_file) DO NOTHING",
                    [(f,) for f in batch],
                )
            migrate_table(cur, "legal_chunks", rows,
                """
                INSERT INTO legal_chunks (
                    source_id, law, section, heading, text, source_file, page_start, page_end
                ) VALUES (
                    %(source_id)s, %(law)s, %(section)s, %(heading)s, %(text)s,
                    %(source_file)s, %(page_start)s, %(page_end)s
                )
                ON CONFLICT (source_id) DO UPDATE SET
                    law        = EXCLUDED.law,
                    section    = EXCLUDED.section,
                    heading    = EXCLUDED.heading,
                    text       = EXCLUDED.text,
                    source_file = EXCLUDED.source_file,
                    page_start = EXCLUDED.page_start,
                    page_end   = EXCLUDED.page_end
                """,
                dict,
            )

            # ── 10. legal_embeddings ─────────────────────────────────────
            print("\n[10] legal_embeddings")
            try:
                emb_rows = sqlite_conn.execute(
                    "SELECT source_id, model, dimensions, vector FROM legal_embeddings"
                ).fetchall()
                if emb_rows:
                    print(f"    {len(emb_rows)} rows found")
                    for batch in batched(emb_rows, BATCH_SIZE):
                        data = [
                            (r["source_id"], r["model"], r["dimensions"],
                             np.frombuffer(r["vector"], dtype=np.float32) if r["vector"] else None)
                            for r in batch
                        ]
                        cur.executemany(
                            """
                            INSERT INTO legal_embeddings (source_id, model, dimensions, vector)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (source_id) DO UPDATE SET
                                model      = EXCLUDED.model,
                                dimensions = EXCLUDED.dimensions,
                                vector     = EXCLUDED.vector
                            """,
                            data,
                        )
                    print(f"    migrated {len(emb_rows)} rows")
                else:
                    print("    (no embeddings)")
            except sqlite3.OperationalError:
                print("    (table not present, skipped)")

            # ── 11. section_reviews ──────────────────────────────────────
            print("\n[11] section_reviews")
            try:
                rows = sqlite_conn.execute("SELECT * FROM section_reviews").fetchall()
                print(f"    {len(rows)} rows found")
                migrate_table(cur, "section_reviews", rows,
                    """
                    INSERT INTO section_reviews (
                        source_id, original_heading, original_text, status, notes, updated_at
                    ) VALUES (
                        %(source_id)s, %(original_heading)s, %(original_text)s, %(status)s, %(notes)s,
                        COALESCE(%(updated_at)s::timestamptz, NOW())
                    )
                    ON CONFLICT (source_id) DO UPDATE SET
                        original_heading = EXCLUDED.original_heading,
                        original_text    = EXCLUDED.original_text,
                        status           = EXCLUDED.status,
                        notes            = EXCLUDED.notes,
                        updated_at       = EXCLUDED.updated_at
                    """,
                    dict,
                )
            except sqlite3.OperationalError:
                print("    (table not present, skipped)")

            # ── 12. legal_relationships ──────────────────────────────────
            print("\n[12] legal_relationships")
            try:
                rows = sqlite_conn.execute("SELECT * FROM legal_relationships").fetchall()
                print(f"    {len(rows)} rows found")
                migrate_table(cur, "legal_relationships", rows,
                    """
                    INSERT INTO legal_relationships (
                        source_file, relation_type, target_law, target_section, evidence, status
                    ) VALUES (
                        %(source_file)s, %(relation_type)s, %(target_law)s, %(target_section)s,
                        %(evidence)s, %(status)s
                    )
                    """,
                    dict,
                )
            except sqlite3.OperationalError:
                print("    (table not present, skipped)")

            # ── 13. audit_log ────────────────────────────────────────────
            print("\n[13] audit_log")
            try:
                rows = sqlite_conn.execute("SELECT * FROM audit_log").fetchall()
                print(f"    {len(rows)} rows found")
                migrate_table(cur, "audit_log", rows,
                    """
                    INSERT INTO audit_log (source_file, action, actor, details, created_at)
                    VALUES (
                        %(source_file)s, %(action)s, %(actor)s, %(details)s,
                        COALESCE(%(created_at)s::timestamptz, NOW())
                    )
                    """,
                    dict,
                )
            except sqlite3.OperationalError:
                print("    (table not present, skipped)")

            # ── 14. archived_metadata ────────────────────────────────────
            print("\n[14] archived_metadata")
            try:
                rows = sqlite_conn.execute("SELECT * FROM archived_metadata").fetchall()
                print(f"    {len(rows)} rows found")
                migrate_table(cur, "archived_metadata", rows,
                    """
                    INSERT INTO archived_metadata (source_file, record_json, reason, archived_at)
                    VALUES (
                        %(source_file)s, %(record_json)s, %(reason)s,
                        COALESCE(%(archived_at)s::timestamptz, NOW())
                    )
                    ON CONFLICT (source_file) DO UPDATE SET
                        record_json = EXCLUDED.record_json,
                        reason      = EXCLUDED.reason,
                        archived_at = EXCLUDED.archived_at
                    """,
                    dict,
                )
            except sqlite3.OperationalError:
                print("    (table not present, skipped)")

            # ── 15. collection_files ─────────────────────────────────────
            print("\n[15] collection_files")
            try:
                rows = sqlite_conn.execute("SELECT * FROM collection_files").fetchall()
                print(f"    {len(rows)} rows found")
                migrate_table(cur, "collection_files", rows,
                    """
                    INSERT INTO collection_files (
                        origin_path, sha256, byte_size, source_file, storage_key,
                        state, pages, extracted_pages, warnings_json, updated_at
                    ) VALUES (
                        %(origin_path)s, %(sha256)s, %(byte_size)s, %(source_file)s, %(storage_key)s,
                        %(state)s, %(pages)s, %(extracted_pages)s, %(warnings_json)s,
                        COALESCE(%(updated_at)s::timestamptz, NOW())
                    )
                    ON CONFLICT (origin_path) DO UPDATE SET
                        sha256          = EXCLUDED.sha256,
                        byte_size       = EXCLUDED.byte_size,
                        source_file     = EXCLUDED.source_file,
                        storage_key     = EXCLUDED.storage_key,
                        state           = EXCLUDED.state,
                        pages           = EXCLUDED.pages,
                        extracted_pages = EXCLUDED.extracted_pages,
                        warnings_json   = EXCLUDED.warnings_json,
                        updated_at      = EXCLUDED.updated_at
                    """,
                    dict,
                )
            except sqlite3.OperationalError:
                print("    (table not present, skipped)")

            # ── 16. admin_credentials ────────────────────────────────────
            print("\n[16] admin_credentials")
            try:
                rows = sqlite_conn.execute("SELECT * FROM admin_credentials").fetchall()
                print(f"    {len(rows)} rows found")
                migrate_table(cur, "admin_credentials", rows,
                    """
                    INSERT INTO admin_credentials (username, password_hash, salt, updated_at)
                    VALUES (
                        %(username)s, %(password_hash)s, %(salt)s,
                        COALESCE(%(updated_at)s::timestamptz, NOW())
                    )
                    ON CONFLICT (username) DO UPDATE SET
                        password_hash = EXCLUDED.password_hash,
                        salt          = EXCLUDED.salt,
                        updated_at    = EXCLUDED.updated_at
                    """,
                    dict,
                )
            except sqlite3.OperationalError:
                print("    (table not present, skipped)")

            # ── 17. site_content ─────────────────────────────────────────
            print("\n[17] site_content")
            try:
                rows = sqlite_conn.execute("SELECT * FROM site_content").fetchall()
                print(f"    {len(rows)} rows found")
                migrate_table(cur, "site_content", rows,
                    """
                    INSERT INTO site_content (id, content_json, updated_at)
                    VALUES (
                        %(id)s, %(content_json)s,
                        COALESCE(%(updated_at)s::timestamptz, NOW())
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        content_json = EXCLUDED.content_json,
                        updated_at   = EXCLUDED.updated_at
                    """,
                    dict,
                )
            except sqlite3.OperationalError:
                print("    (table not present, skipped)")

        pg.commit()

    sqlite_conn.close()
    print(f"\n{'='*60}")
    print("  [SUCCESS] Migration complete!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    migrate()

