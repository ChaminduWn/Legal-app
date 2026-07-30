from datetime import UTC, datetime
from pathlib import Path

from app.models import (
    AdminSection,
    DocumentMetadataUpdate,
    DocumentSummary,
    SectionReviewUpdate,
)
from app.rag.store import connect, initialize


def initialize_admin(database: Path) -> None:
    initialize(database)
    with connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS document_metadata (
                source_file TEXT PRIMARY KEY,
                official_source_url TEXT,
                publication_date TEXT,
                effective_date TEXT,
                status TEXT NOT NULL DEFAULT 'unreviewed',
                notes TEXT,
                updated_at TEXT NOT NULL
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
            """
        )


def list_documents(database: Path) -> list[DocumentSummary]:
    initialize_admin(database)
    with connect(database) as connection:
        rows = connection.execute(
            """
            SELECT
                c.source_file,
                MIN(c.law) AS law,
                COUNT(*) AS section_count,
                SUM(CASE WHEN r.status = 'verified' THEN 1 ELSE 0 END)
                    AS verified_sections,
                COALESCE(m.status, 'unreviewed') AS status,
                m.official_source_url,
                m.publication_date,
                m.effective_date,
                m.notes
            FROM legal_chunks c
            LEFT JOIN section_reviews r USING (source_id)
            LEFT JOIN document_metadata m USING (source_file)
            GROUP BY c.source_file
            ORDER BY law, c.source_file
            """
        ).fetchall()
    return [DocumentSummary.model_validate(dict(row)) for row in rows]


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
             status, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_file) DO UPDATE SET
                official_source_url = excluded.official_source_url,
                publication_date = excluded.publication_date,
                effective_date = excluded.effective_date,
                status = excluded.status,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                source_file,
                update.official_source_url,
                update.publication_date,
                update.effective_date,
                update.status,
                update.notes,
                now,
            ),
        )


def save_section_review(
    database: Path, source_id: str, update: SectionReviewUpdate
) -> None:
    initialize_admin(database)
    now = datetime.now(UTC).isoformat()
    with connect(database) as connection:
        current = connection.execute(
            "SELECT heading, text, law, section FROM legal_chunks WHERE source_id = ?",
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
