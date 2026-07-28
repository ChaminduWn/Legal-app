from pathlib import Path

from app.admin import (
    document_sections,
    list_documents,
    save_document_metadata,
    save_section_review,
)
from app.models import DocumentMetadataUpdate, SectionReviewUpdate
from app.rag.schema import LegalChunk
from app.rag.store import connect, replace_chunks


def seed(database: Path) -> None:
    replace_chunks(
        database,
        [
            LegalChunk(
                source_id="penal-code-section-366",
                law="Penal Code",
                section="366",
                heading="Theft",
                text="Whoever dishonestly takes movable property commits theft.",
                source_file="penal.pdf",
                page_start=10,
                page_end=10,
            )
        ],
    )


def test_document_metadata_and_section_review_are_persisted(tmp_path: Path) -> None:
    database = tmp_path / "index.sqlite3"
    seed(database)

    save_document_metadata(
        database,
        "penal.pdf",
        DocumentMetadataUpdate(
            official_source_url="https://example.gov/penal.pdf",
            publication_date="2024",
            effective_date=None,
            status="in_review",
            notes="Draft source record",
        ),
    )
    save_section_review(
        database,
        "penal-code-section-366",
        SectionReviewUpdate(
            heading="Definition of theft",
            text="Whoever dishonestly takes movable property commits the offence of theft.",
            status="verified",
            notes="Compared with page 10",
        ),
    )

    document = list_documents(database)[0]
    section = document_sections(database, "penal.pdf")[0]
    assert document.status == "in_review"
    assert document.verified_sections == 1
    assert section.status == "verified"
    assert section.heading == "Definition of theft"
    assert section.semantic_indexed is False

    with connect(database) as connection:
        original = connection.execute(
            "SELECT original_text FROM section_reviews"
        ).fetchone()[0]
    assert original == "Whoever dishonestly takes movable property commits theft."

