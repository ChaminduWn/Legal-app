from contextlib import nullcontext
from datetime import UTC, datetime, date
from urllib.parse import urlsplit
from pathlib import Path
import json

from app.models import (
    AdminSection,
    DocumentMetadataUpdate,
    DocumentSummary,
    SectionReviewUpdate,
    LegalRelationship,
    AuditEvent,
)
from app.db import connect, is_postgres
from app.rag.store import initialize
from app.catalog import save_version
from app.auth import initialize_auth_table
from app.site_content import initialize_site_content


def initialize_admin(database: Path) -> None:
    initialize(database)
    with connect(database) as connection:
        initialize_auth_table(connection)
        initialize_site_content(connection)
        if not is_postgres(connection):
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS document_metadata (
                    source_file TEXT PRIMARY KEY,
                    official_source_url TEXT,
                    publication_date TEXT,
                    effective_date TEXT,
                    status TEXT NOT NULL DEFAULT 'unreviewed',
                    notes TEXT,
                    updated_at TEXT NOT NULL,
                    document_type TEXT NOT NULL DEFAULT 'other',
                    extraction_method TEXT NOT NULL DEFAULT 'text',
                    extraction_confidence REAL,
                    extraction_warnings TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS section_reviews (
                    source_id TEXT PRIMARY KEY,
                    original_heading TEXT,
                    original_text TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unreviewed',
                    notes TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(source_id) REFERENCES legal_chunks(source_id)
                );
                CREATE TABLE IF NOT EXISTS legal_relationships (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_file TEXT NOT NULL,
                    relation_type TEXT NOT NULL, target_law TEXT NOT NULL,
                    target_section TEXT, evidence TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed'
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_file TEXT NOT NULL,
                    action TEXT NOT NULL, actor TEXT NOT NULL DEFAULT 'admin',
                    details TEXT, created_at TEXT NOT NULL
                );
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(document_metadata)")}
            migrations = {
                "official_source_url": "TEXT",
                "publication_date": "TEXT",
                "effective_date": "TEXT",
                "notes": "TEXT",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
                "document_type": "TEXT NOT NULL DEFAULT 'other'",
                "extraction_method": "TEXT NOT NULL DEFAULT 'text'",
                "extraction_confidence": "REAL",
                "extraction_warnings": "TEXT NOT NULL DEFAULT '[]'",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE document_metadata ADD COLUMN {name} {definition}")


def list_documents(database: Path) -> list[DocumentSummary]:
    initialize_admin(database)
    with connect(database) as connection:
        rows = connection.execute(
            """
            SELECT
                d.source_file,
                COALESCE(MIN(c.law),d.title) AS law,
                COUNT(c.source_id) AS section_count,
                SUM(CASE WHEN r.status = 'verified' THEN 1 ELSE 0 END)
                    AS verified_sections,
                COALESCE(m.status, 'unreviewed') AS status,
                m.official_source_url,
                m.publication_date,
                m.effective_date,
                m.notes, v.instrument_id, i.title AS instrument_title, v.edition_year, v.version_kind,
                v.valid_from, v.valid_to, v.supersedes_source,
                vr.target_instrument_id, vr.relation_type, vr.evidence AS relationship_evidence
                ,COALESCE(m.document_type, 'other') AS document_type
                ,COALESCE(m.extraction_method, 'text') AS extraction_method
                ,m.extraction_confidence, COALESCE(m.extraction_warnings::text, '[]') AS extraction_warnings
                ,(SELECT COUNT(*) FROM legal_relationships lr WHERE lr.source_file=d.source_file) AS relationship_count
            FROM document_records d
            LEFT JOIN legal_chunks c USING (source_file)
            LEFT JOIN section_reviews r USING (source_id)
            LEFT JOIN document_metadata m USING (source_file)
            LEFT JOIN document_versions v USING (source_file)
            LEFT JOIN legal_instruments i ON i.instrument_id=v.instrument_id
            LEFT JOIN version_relationships vr USING (source_file)
            GROUP BY 
                d.source_file, d.title, m.status, m.official_source_url, 
                m.publication_date, m.effective_date, m.notes, v.instrument_id, 
                i.title, v.edition_year, v.version_kind, v.valid_from, v.valid_to, 
                v.supersedes_source, vr.target_instrument_id, vr.relation_type, 
                vr.evidence, m.document_type, m.extraction_method, 
                m.extraction_confidence, m.extraction_warnings
            ORDER BY law, d.source_file
            """
        ).fetchall()
        topics = {}
        for topic in connection.execute("SELECT source_file,topic_id FROM document_topics"):
            topics.setdefault(topic[0],[]).append(topic[1])

    result = []
    for row in rows:
        data = dict(row)
        raw_warn = data.get("extraction_warnings")
        if isinstance(raw_warn, str):
            try:
                data["extraction_warnings"] = json.loads(raw_warn)
            except Exception:
                data["extraction_warnings"] = []
        elif not isinstance(raw_warn, list):
            data["extraction_warnings"] = []

        data["topic_ids"] = topics.get(data["source_file"], [])
        result.append(DocumentSummary.model_validate(data))
    return result


def document_sections(database: Path, source_file: str) -> list[AdminSection]:
    initialize_admin(database)
    with connect(database) as connection:
        rows = connection.execute(
            """
            SELECT
                c.source_id, c.section, c.heading, c.text,
                c.page_start, c.page_end,
                COALESCE(r.status, 'unreviewed') AS status,
                r.notes,
                CASE WHEN e.source_id IS NULL THEN 0 ELSE 1 END AS semantic_indexed
            FROM legal_chunks c
            LEFT JOIN section_reviews r USING (source_id)
            LEFT JOIN legal_embeddings e USING (source_id)
            WHERE c.source_file = ?
            ORDER BY c.page_start, LENGTH(c.section), c.section
            """,
            (source_file,),
        ).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        data["semantic_indexed"] = bool(data["semantic_indexed"])
        result.append(AdminSection.model_validate(data))
    return result


def save_document_metadata(
    database: Path, source_file: str, update: DocumentMetadataUpdate
) -> None:
    initialize_admin(database)
    if update.status == "verified":
        raise ValueError("Use Approve & publish after completing the review checks.")
    now = datetime.now(UTC).isoformat()
    with connect(database) as connection:
        exists = connection.execute(
            "SELECT 1 FROM legal_chunks WHERE source_file = ? LIMIT 1",
            (source_file,),
        ).fetchone()
        if not exists:
            raise KeyError(source_file)
        connection.execute(
            """
            INSERT INTO document_metadata
            (source_file, official_source_url, publication_date, effective_date,
             status, notes, updated_at, document_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_file) DO UPDATE SET
                official_source_url = excluded.official_source_url,
                publication_date = excluded.publication_date,
                effective_date = excluded.effective_date,
                status = excluded.status,
                notes = excluded.notes,
                updated_at = excluded.updated_at
                ,document_type = excluded.document_type
            """,
            (
                source_file,
                update.official_source_url,
                update.publication_date,
                update.effective_date,
                update.status,
                update.notes,
                now,
                update.document_type,
            ),
        )
        if update.version is not None:
            save_version(connection, source_file, update.version)
        if update.topic_ids is not None:
            known = {r[0] for r in connection.execute("SELECT topic_id FROM legal_topics")}
            if not set(update.topic_ids).issubset(known):
                raise ValueError("Choose an existing legal topic.")
            connection.execute("DELETE FROM document_topics WHERE source_file=?", (source_file,))
            connection.executemany("INSERT INTO document_topics VALUES (?,?)", [(source_file,t) for t in set(update.topic_ids)])
        _audit(connection, source_file, "metadata_updated", update.model_dump_json())


def save_section_review(
    database: Path, source_id: str, update: SectionReviewUpdate
) -> None:
    initialize_admin(database)
    now = datetime.now(UTC).isoformat()
    with connect(database) as connection:
        current = connection.execute(
            "SELECT heading, text, law, section, source_file FROM legal_chunks WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        if not current:
            raise KeyError(source_id)
        connection.execute(
            """
            INSERT INTO section_reviews
            (source_id, original_heading, original_text, status, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                status = excluded.status,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                source_id,
                current["heading"],
                current["text"],
                update.status,
                update.notes,
                now,
            ),
        )
        connection.execute(
            "UPDATE legal_chunks SET heading = ?, text = ? WHERE source_id = ?",
            (update.heading, update.text, source_id),
        )
        if not is_postgres(connection):
            connection.execute(
                """
                UPDATE legal_chunks_fts
                SET heading = ?, text = ?
                WHERE source_id = ?
                """,
                (update.heading, update.text, source_id),
            )
        connection.execute(
            "DELETE FROM legal_embeddings WHERE source_id = ?",
            (source_id,),
        )
        connection.execute("UPDATE document_metadata SET status='in_review', updated_at=? WHERE source_file=? AND status='verified'", (now, current["source_file"]))
        _audit(connection, current["source_file"], "section_reviewed", f"{source_id}: {update.status}")


def register_import(database: Path, source_file: str, document_type: str, method: str,
                    confidence: float | None, warnings: list[str], chunks: list, *, connection=None) -> None:
    now = datetime.now(UTC).isoformat()
    with (nullcontext(connection) if connection is not None else connect(database)) as connection:
        warn_val = json.dumps(warnings) if not is_postgres(connection) else warnings
        connection.execute("""INSERT INTO document_metadata
            (source_file,status,updated_at,document_type,extraction_method,extraction_confidence,extraction_warnings)
            VALUES (?, 'in_review', ?, ?, ?, ?, ?) ON CONFLICT(source_file) DO UPDATE SET status='in_review', updated_at=excluded.updated_at, document_type=excluded.document_type, extraction_method=excluded.extraction_method, extraction_confidence=excluded.extraction_confidence, extraction_warnings=excluded.extraction_warnings""",
            (source_file, now, document_type, method, confidence, warn_val))
        _detect_relationships(connection, source_file, chunks)
        _audit(connection, source_file, "document_imported", f"{method}; {len(chunks)} sections")


def approve_document(database: Path, source_file: str, *, extraction_complete: bool = False, authority_checked: bool = False) -> None:
    initialize_admin(database)
    now = datetime.now(UTC).isoformat()
    with connect(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        total, verified = connection.execute("""SELECT COUNT(*), SUM(CASE WHEN r.status='verified' THEN 1 ELSE 0 END)
            FROM legal_chunks c LEFT JOIN section_reviews r USING(source_id) WHERE c.source_file=?""", (source_file,)).fetchone()
        if not total: raise KeyError(source_file)
        if verified != total: raise ValueError("Verify every extracted section before publishing.")
        metadata = connection.execute("SELECT * FROM document_metadata WHERE source_file=?", (source_file,)).fetchone()
        if not metadata or metadata["status"] == "superseded":
            raise ValueError("Save document details with In review status before publishing.")
        from app.config import get_settings
        if get_settings().environment == "production":
            for field in ("official_source_url", "publication_date", "effective_date"):
                if not metadata[field]:
                    raise ValueError(f"Production publication requires {field.replace('_', ' ')}.")
            version = connection.execute("SELECT * FROM document_versions WHERE source_file=?", (source_file,)).fetchone()
            if not version:
                raise ValueError("Production publication requires a verified edition year and effective period.")
            if metadata['document_type'] == 'amendment' and not connection.execute("SELECT 1 FROM version_relationships WHERE source_file=? AND relation_type IN ('amends','repeals')", (source_file,)).fetchone():
                raise ValueError("Link this amendment to the affected law before publication.")
        url = urlsplit(metadata["official_source_url"] or "")
        if metadata["official_source_url"] and (url.scheme not in {"https", "http"} or not url.hostname):
            raise ValueError("Enter a valid official source URL in Document details.")
        for field in ("publication_date", "effective_date"):
            value = metadata[field]
            if not value:
                continue
            try:
                parsed = date.fromisoformat(value)
                if parsed.isoformat() != value:
                    raise ValueError()
            except ValueError:
                raise ValueError(f"Enter a valid {field.replace('_', ' ')} (YYYY-MM-DD).") from None
            if parsed > date.today():
                raise ValueError(f"The {field.replace('_', ' ')} is in the future; keep this document in review.")
        if not extraction_complete or not authority_checked:
            raise ValueError("Confirm extraction completeness and source authority before publishing.")
        connection.execute("UPDATE document_metadata SET status='verified', updated_at=? WHERE source_file=?", (now, source_file))
        _audit(connection, source_file, "document_published", f"{total} verified sections; reviewer confirmed complete extraction and source authority; current-law version not automatically verified")


def relationships(database: Path, source_file: str) -> list[LegalRelationship]:
    initialize_admin(database)
    with connect(database) as connection:
        rows = connection.execute("SELECT * FROM legal_relationships WHERE source_file=?", (source_file,)).fetchall()
    return [LegalRelationship.model_validate(dict(row)) for row in rows]


def audit_events(database: Path, source_file: str) -> list[AuditEvent]:
    initialize_admin(database)
    with connect(database) as connection:
        rows = connection.execute("SELECT id,action,actor,details,created_at FROM audit_log WHERE source_file=? ORDER BY id DESC", (source_file,)).fetchall()
    return [AuditEvent.model_validate(dict(row)) for row in rows]


def _audit(connection, source_file: str, action: str, details: str) -> None:
    connection.execute("INSERT INTO audit_log(source_file,action,details,created_at) VALUES(?,?,?,?)",
                       (source_file, action, details, datetime.now(UTC).isoformat()))


def _detect_relationships(connection, source_file: str, chunks: list) -> None:
    import re
    pattern = re.compile(r"(?i)(amend(?:s|ed|ment)?|repeal(?:s|ed)?|substitut(?:e|es|ed))[^.\n]{0,100}?(?:section\s+([0-9A-Za-z()]+)\s+of\s+)?(?:the\s+)?([A-Z][A-Za-z ()'-]{3,80}?(?:Act|Ordinance|Code))")
    for chunk in chunks:
        for match in pattern.finditer(chunk.text):
            connection.execute("INSERT INTO legal_relationships(source_file,relation_type,target_law,target_section,evidence) VALUES(?,?,?,?,?)",
                (source_file, match.group(1).lower(), match.group(3).strip(), match.group(2), match.group(0)[:300]))


def resolve_pdf(workspace: Path, source_file: str) -> Path | None:
    relative = Path(source_file.replace("\\", "/"))
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.suffix.lower() != ".pdf"
    ):
        return None
    candidates = [
        workspace / relative,
        workspace / "data" / "uploads" / relative,
        workspace / "step 001 pdf folder" / relative,
        workspace / "Criminal law" / relative,
        workspace / "Civil Law" / relative,
        workspace / "පනත්" / relative,
    ]
    workspace_resolved = workspace.resolve()
    for candidate in candidates:
        resolved = candidate.resolve()
        if (
            resolved.is_relative_to(workspace_resolved)
            and resolved.is_file()
            and resolved.suffix.lower() == ".pdf"
        ):
            return resolved
    return None
