"""Relational document identities and explicitly reviewed legal editions.

An amendment is a separate instrument, not a replacement consolidated edition.
No dates or supersession relationships are inferred from filenames.
"""
from datetime import date
from pathlib import Path
from app.db import connect, is_postgres


def initialize_catalog(connection) -> None:
    if is_postgres(connection):
        return

    connection.executescript("""
        CREATE TABLE IF NOT EXISTS document_records (
            source_file TEXT PRIMARY KEY,
            title TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS legal_instruments (
            instrument_id TEXT PRIMARY KEY,
            title TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS document_versions (
            source_file TEXT PRIMARY KEY REFERENCES document_records(source_file),
            instrument_id TEXT NOT NULL REFERENCES legal_instruments(instrument_id),
            edition_year INTEGER NOT NULL CHECK(edition_year BETWEEN 1500 AND 9999),
            version_kind TEXT NOT NULL CHECK(version_kind IN ('original','consolidated','amendment','reference')),
            valid_from TEXT NOT NULL,
            valid_to TEXT,
            supersedes_source TEXT REFERENCES document_versions(source_file),
            CHECK(valid_to IS NULL OR valid_to > valid_from),
            CHECK(supersedes_source IS NULL OR supersedes_source != source_file)
        );
        CREATE TABLE IF NOT EXISTS version_relationships (
            source_file TEXT PRIMARY KEY REFERENCES document_versions(source_file),
            target_instrument_id TEXT NOT NULL REFERENCES legal_instruments(instrument_id),
            relation_type TEXT NOT NULL CHECK(relation_type IN ('amends','repeals','references')),
            evidence TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS versions_instrument_date ON document_versions(instrument_id,valid_from);
        CREATE TABLE IF NOT EXISTS document_assets (
            source_file TEXT NOT NULL REFERENCES document_records(source_file),
            page_number INTEGER NOT NULL DEFAULT 0 CHECK(page_number >= 0),
            storage_key TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
            media_type TEXT NOT NULL,
            original_name TEXT NOT NULL,
            PRIMARY KEY(source_file,page_number)
        );
        CREATE TABLE IF NOT EXISTS legal_topics (
            topic_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            parent_id TEXT REFERENCES legal_topics(topic_id)
        );
        CREATE TABLE IF NOT EXISTS document_topics (
            source_file TEXT NOT NULL REFERENCES document_records(source_file),
            topic_id TEXT NOT NULL REFERENCES legal_topics(topic_id),
            PRIMARY KEY(source_file,topic_id)
        );
    """)

    roots = [("civil", "Civil law"), ("criminal", "Criminal law"), ("commercial", "Commercial law"),
             ("international", "International law"), ("constitutional", "Administrative and constitutional law"),
             ("environmental", "Environmental law"), ("sea", "Law of the sea")]
    connection.executemany("INSERT OR IGNORE INTO legal_topics(topic_id,name) VALUES (?,?)", roots)
    children = {
        "civil": ["Family", "Land", "Delict", "Inheritance"],
        "criminal": ["Evidence", "Criminal procedure"],
        "commercial": ["Company", "Business", "Intellectual property", "Media", "Contract", "Labour", "Tax"],
        "international": ["Public international", "Private international", "International institutions", "Humanitarian", "International investment"],
    }
    for parent, names in children.items():
        connection.executemany("INSERT OR IGNORE INTO legal_topics VALUES (?,?,?)",
            [(parent + "-" + name.lower().replace(" ", "-"), name, parent) for name in names])


def save_version(connection, source_file: str, version) -> None:
    """Called in the same transaction as metadata withdrawal."""
    start = date.fromisoformat(version.valid_from).isoformat()
    end = date.fromisoformat(version.valid_to).isoformat() if version.valid_to else None
    if end and end <= start:
        raise ValueError("The end date must be after the effective date.")
    if version.supersedes_source:
        previous = connection.execute(
            "SELECT * FROM document_versions WHERE source_file=?", (version.supersedes_source,)
        ).fetchone()
        if not previous or previous['instrument_id'] != version.instrument_id:
            raise ValueError("The previous edition must belong to the same law.")
        if version.version_kind != 'consolidated' or previous['version_kind'] not in ('original','consolidated'):
            raise ValueError("Only a complete consolidated edition can replace a complete earlier edition; amendments stay separate.")
        if previous['valid_from'] >= start:
            raise ValueError("The replacement must take effect after the previous edition.")
        if previous['valid_to'] != start:
            raise ValueError("First set the earlier edition's end date to this edition's start date.")
    overlaps = connection.execute("""
        SELECT source_file FROM document_versions WHERE instrument_id=? AND source_file != ?
        AND version_kind IN ('original','consolidated')
        AND valid_from < COALESCE(?, '9999-12-31') AND COALESCE(valid_to,'9999-12-31') > ?
    """, (version.instrument_id, source_file, end, start)).fetchall()
    if version.version_kind in ('original','consolidated') and overlaps:
        raise ValueError("Complete editions of the same law cannot have overlapping effective periods.")
    dependents = connection.execute("SELECT valid_from FROM document_versions WHERE supersedes_source=?", (source_file,)).fetchall()
    if any(row['valid_from'] != end for row in dependents):
        raise ValueError("The end date must remain aligned with the replacement edition's start date.")
    existing = connection.execute("SELECT instrument_id FROM document_versions WHERE source_file=?", (source_file,)).fetchone()
    if dependents and existing and existing['instrument_id'] != version.instrument_id:
        raise ValueError("A linked edition cannot be moved to a different law.")
    connection.execute("INSERT INTO legal_instruments(instrument_id,title) VALUES (?,?) ON CONFLICT(instrument_id) DO NOTHING",
                       (version.instrument_id, version.instrument_title))
    connection.execute("""INSERT INTO document_versions VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(source_file) DO UPDATE SET instrument_id=excluded.instrument_id,
        edition_year=excluded.edition_year,version_kind=excluded.version_kind,
        valid_from=excluded.valid_from,valid_to=excluded.valid_to,supersedes_source=excluded.supersedes_source
    """, (source_file, version.instrument_id, version.edition_year, version.version_kind,
           start, end, version.supersedes_source))

    if version.target_instrument_id:
        if version.target_instrument_id == version.instrument_id:
            raise ValueError("An amendment must link to a different target law.")
        if not connection.execute("SELECT 1 FROM legal_instruments WHERE instrument_id=?", (version.target_instrument_id,)).fetchone():
            raise ValueError("Create the target law's edition record before linking this amendment.")
        if not version.relationship_evidence or not version.relationship_evidence.strip():
            raise ValueError("Record the provision that establishes this legal relationship.")
        connection.execute("INSERT INTO version_relationships VALUES (?,?,?,?) ON CONFLICT(source_file) DO UPDATE SET target_instrument_id=excluded.target_instrument_id,relation_type=excluded.relation_type,evidence=excluded.evidence",
            (source_file,version.target_instrument_id,version.relation_type,version.relationship_evidence))
    else:
        connection.execute("DELETE FROM version_relationships WHERE source_file=?", (source_file,))


def migrate_chunk_foreign_key(database: Path) -> None:
    """Preserve IDs and dependent reviews while adding the missing document FK."""
    conn = connect(database)
    if is_postgres(conn):
        conn.close()
        return

    import sqlite3
    connection = sqlite3.connect(database)
    try:
        if connection.execute('PRAGMA foreign_key_list(legal_chunks)').fetchall():
            return
        connection.execute('PRAGMA foreign_keys=OFF')
        connection.execute('BEGIN IMMEDIATE')
        connection.execute("INSERT OR IGNORE INTO document_records SELECT source_file,MIN(law) FROM legal_chunks GROUP BY source_file")
        connection.execute("""CREATE TABLE legal_chunks_new (
            source_id TEXT PRIMARY KEY, law TEXT NOT NULL, section TEXT NOT NULL,
            heading TEXT, text TEXT NOT NULL,
            source_file TEXT NOT NULL REFERENCES document_records(source_file),
            page_start INTEGER NOT NULL CHECK(page_start>0),
            page_end INTEGER NOT NULL CHECK(page_end>=page_start))""")
        connection.execute('INSERT INTO legal_chunks_new SELECT * FROM legal_chunks')
        connection.execute('DROP TABLE legal_chunks')
        connection.execute('ALTER TABLE legal_chunks_new RENAME TO legal_chunks')
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Migration found orphaned records; restore or repair them before migrating.')
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
