import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
import numpy as np

try:
    import psycopg
    from pgvector.psycopg import register_vector
except ImportError:
    print("[!] 'psycopg' or 'pgvector' is not installed.")
    print("    Run: pip install \"psycopg[binary]>=3.1\" pgvector")
    exit(1)

from app.config import get_settings

SQLITE_PATH = get_settings().legal_ai_database
BACKUP_DIR = Path("data/backups")


def backup_sqlite():
    """Create a timestamped backup of the local SQLite database."""
    if not SQLITE_PATH.exists():
        print(f"[-] SQLite database not found at {SQLITE_PATH}")
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"legal_index_backup_{timestamp}.sqlite3"
    shutil.copy2(SQLITE_PATH, backup_path)
    print(f"[+] SQLite database backed up to: {backup_path}")
    return backup_path


import urllib.parse

def get_connection(database_url: str):
    """Attempt direct connection, and fallback to IPv4 Pooler if DNS/IPv6 fails."""
    try:
        return psycopg.connect(database_url, autocommit=False, connect_timeout=10)
    except Exception as direct_err:
        print(f"[-] Direct connection attempt: {direct_err}")
        # Parse database_url to check if we can connect via Supabase Pooler
        try:
            parsed = urllib.parse.urlparse(database_url)
            host = parsed.hostname or ""
            if host.startswith("db.") and host.endswith(".supabase.co"):
                project_ref = host.split(".")[1]
                password = urllib.parse.unquote(parsed.password or "")
                db = parsed.path.lstrip("/") or "postgres"
                pooler_host = "aws-0-ap-southeast-1.pooler.supabase.com"
                pooler_user = f"postgres.{project_ref}"
                print(f"[*] Falling back to IPv4 Pooler ({pooler_host}:5432 with user {pooler_user})...")
                return psycopg.connect(
                    host=pooler_host,
                    port=5432,
                    user=pooler_user,
                    password=password,
                    dbname=db,
                    autocommit=False,
                    connect_timeout=10,
                )
        except Exception as pooler_err:
            print(f"[-] Pooler fallback error: {pooler_err}")
        raise direct_err


def migrate(database_url: str):
    print("\n--- Starting Migration to Supabase ---")
    backup_sqlite()

    if not SQLITE_PATH.exists():
        print(f"[-] SQLite file {SQLITE_PATH} does not exist.")
        return

    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    if sqlite_conn.execute("SELECT 1 FROM sqlite_master WHERE name='document_versions'").fetchone():
        sqlite_conn.close()
        raise SystemExit("This legacy export does not migrate the relational catalog, original assets, or version history. It is disabled for the upgraded database. The running app still uses SQLite; a PostgreSQL runtime adapter and complete migration are required before switching.")

    print(f"[+] Connecting to Supabase...")
    with get_connection(database_url) as pg_conn:

        with pg_conn.cursor() as cur:
            # 0. Initialize Schema & Extensions
            print("[*] Initializing Supabase schema and pgvector extension...")
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS document_metadata (
                    source_file TEXT PRIMARY KEY,
                    official_source_url TEXT,
                    publication_date TEXT,
                    effective_date TEXT,
                    status TEXT NOT NULL DEFAULT 'unreviewed',
                    notes TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    document_type TEXT NOT NULL DEFAULT 'other',
                    extraction_method TEXT NOT NULL DEFAULT 'text',
                    extraction_confidence REAL,
                    extraction_warnings JSONB NOT NULL DEFAULT '[]'::jsonb
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS legal_chunks (
                    source_id TEXT PRIMARY KEY,
                    law TEXT NOT NULL,
                    section TEXT NOT NULL,
                    heading TEXT,
                    text TEXT NOT NULL,
                    source_file TEXT NOT NULL,
                    page_start INTEGER NOT NULL,
                    page_end INTEGER NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS legal_embeddings (
                    source_id TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector vector
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS section_reviews (
                    source_id TEXT PRIMARY KEY,
                    original_heading TEXT,
                    original_text TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unreviewed',
                    notes TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS legal_relationships (
                    id BIGSERIAL PRIMARY KEY,
                    source_file TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    target_law TEXT NOT NULL,
                    target_section TEXT,
                    evidence TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'proposed'
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id BIGSERIAL PRIMARY KEY,
                    source_file TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT 'admin',
                    details TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
        pg_conn.commit()

        register_vector(pg_conn)
        with pg_conn.cursor() as cur:
            # 1. Migrate document_metadata
            doc_rows = sqlite_conn.execute("SELECT * FROM document_metadata").fetchall()
            print(f"[*] Migrating {len(doc_rows)} document_metadata records...", flush=True)
            if doc_rows:
                cur.executemany(
                    """
                    INSERT INTO document_metadata (
                        source_file, official_source_url, publication_date, effective_date,
                        status, notes, updated_at, document_type, extraction_method,
                        extraction_confidence, extraction_warnings
                    ) VALUES (
                        %(source_file)s, %(official_source_url)s, %(publication_date)s, %(effective_date)s,
                        %(status)s, %(notes)s, COALESCE(%(updated_at)s::timestamptz, NOW()),
                        COALESCE(%(document_type)s, 'other'), COALESCE(%(extraction_method)s, 'text'),
                        %(extraction_confidence)s, COALESCE(%(extraction_warnings)s::jsonb, '[]'::jsonb)
                    ) ON CONFLICT (source_file) DO UPDATE SET
                        official_source_url = EXCLUDED.official_source_url,
                        publication_date = EXCLUDED.publication_date,
                        effective_date = EXCLUDED.effective_date,
                        status = EXCLUDED.status,
                        notes = EXCLUDED.notes,
                        updated_at = EXCLUDED.updated_at,
                        document_type = EXCLUDED.document_type;
                    """,
                    [dict(r) for r in doc_rows],
                )

            # 2. Migrate legal_chunks
            chunk_rows = sqlite_conn.execute("SELECT * FROM legal_chunks").fetchall()
            print(f"[*] Migrating {len(chunk_rows)} legal_chunks...", flush=True)
            if chunk_rows:
                # Ensure all source_files exist in document_metadata
                unique_files = list({r["source_file"] for r in chunk_rows})
                cur.executemany(
                    "INSERT INTO document_metadata (source_file, status) VALUES (%s, 'unreviewed') ON CONFLICT (source_file) DO NOTHING",
                    [(f,) for f in unique_files],
                )
                cur.executemany(
                    """
                    INSERT INTO legal_chunks (
                        source_id, law, section, heading, text, source_file, page_start, page_end
                    ) VALUES (
                        %(source_id)s, %(law)s, %(section)s, %(heading)s, %(text)s, %(source_file)s, %(page_start)s, %(page_end)s
                    ) ON CONFLICT (source_id) DO UPDATE SET
                        law = EXCLUDED.law,
                        section = EXCLUDED.section,
                        heading = EXCLUDED.heading,
                        text = EXCLUDED.text,
                        source_file = EXCLUDED.source_file,
                        page_start = EXCLUDED.page_start,
                        page_end = EXCLUDED.page_end;
                    """,
                    [dict(r) for r in chunk_rows],
                )

            # 3. Migrate legal_embeddings
            try:
                emb_rows = sqlite_conn.execute("SELECT source_id, model, dimensions, vector FROM legal_embeddings").fetchall()
                if emb_rows:
                    print(f"[*] Migrating {len(emb_rows)} legal_embeddings...", flush=True)
                    emb_data = [
                        (r["source_id"], r["model"], r["dimensions"], np.frombuffer(r["vector"], dtype=np.float32))
                        for r in emb_rows
                    ]
                    cur.executemany(
                        """
                        INSERT INTO legal_embeddings (source_id, model, dimensions, vector)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (source_id) DO UPDATE SET
                            model = EXCLUDED.model,
                            dimensions = EXCLUDED.dimensions,
                            vector = EXCLUDED.vector;
                        """,
                        emb_data,
                    )
            except sqlite3.OperationalError:
                print("[-] No legal_embeddings table found in SQLite.", flush=True)

            # 4. Migrate section_reviews
            try:
                review_rows = sqlite_conn.execute("SELECT * FROM section_reviews").fetchall()
                if review_rows:
                    print(f"[*] Migrating {len(review_rows)} section_reviews...", flush=True)
                    cur.executemany(
                        """
                        INSERT INTO section_reviews (
                            source_id, original_heading, original_text, status, notes, updated_at
                        ) VALUES (
                            %(source_id)s, %(original_heading)s, %(original_text)s, %(status)s, %(notes)s,
                            COALESCE(%(updated_at)s::timestamptz, NOW())
                        ) ON CONFLICT (source_id) DO UPDATE SET
                            original_heading = EXCLUDED.original_heading,
                            original_text = EXCLUDED.original_text,
                            status = EXCLUDED.status,
                            notes = EXCLUDED.notes,
                            updated_at = EXCLUDED.updated_at;
                        """,
                        [dict(r) for r in review_rows],
                    )
            except sqlite3.OperationalError:
                pass

            # 5. Migrate legal_relationships
            try:
                rel_rows = sqlite_conn.execute("SELECT * FROM legal_relationships").fetchall()
                if rel_rows:
                    print(f"[*] Migrating {len(rel_rows)} legal_relationships...", flush=True)
                    cur.executemany(
                        """
                        INSERT INTO legal_relationships (
                            source_file, relation_type, target_law, target_section, evidence, status
                        ) VALUES (
                            %(source_file)s, %(relation_type)s, %(target_law)s, %(target_section)s, %(evidence)s, %(status)s
                        );
                        """,
                        [dict(r) for r in rel_rows],
                    )
            except sqlite3.OperationalError:
                pass

            # 6. Migrate audit_log
            try:
                audit_rows = sqlite_conn.execute("SELECT * FROM audit_log").fetchall()
                if audit_rows:
                    print(f"[*] Migrating {len(audit_rows)} audit_log records...", flush=True)
                    cur.executemany(
                        """
                        INSERT INTO audit_log (
                            source_file, action, actor, details, created_at
                        ) VALUES (
                            %(source_file)s, %(action)s, %(actor)s, %(details)s, COALESCE(%(created_at)s::timestamptz, NOW())
                        );
                        """,
                        [dict(r) for r in audit_rows],
                    )
            except sqlite3.OperationalError:
                pass

        pg_conn.commit()
    print("\n[SUCCESS] Successfully migrated all database data to Supabase and backed up SQLite!", flush=True)



if __name__ == "__main__":
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        # Check .env file directly if available
        env_file = Path(".env")
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABASE_URL="):
                    db_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    if not db_url:
        print("[!] Error: DATABASE_URL not found in environment or .env file.")
        print("    Please add DATABASE_URL to your .env file:")
        print("    DATABASE_URL=postgresql://postgres:[PASSWORD]@db.nxiewgalqolrcxoahmrw.supabase.co:5432/postgres")
        exit(1)

    migrate(db_url)
