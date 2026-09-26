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



import pytest
from app.admin import approve_document
from app.rag.store import _keyword_search


def reviewed_document(database):
    seed(database)
    save_document_metadata(database, "penal.pdf", DocumentMetadataUpdate(
        status="in_review", document_type="act", official_source_url="https://example.gov/act.pdf",
        publication_date="2024-01-01", effective_date="2024-02-01"))
    save_section_review(database, "penal-code-section-366", SectionReviewUpdate(
        text="Whoever dishonestly takes movable property commits theft.", status="verified"))


def publish(database):
    approve_document(database, "penal.pdf", extraction_complete=True, authority_checked=True)


def test_publication_and_withdrawal(tmp_path):
    database = tmp_path / "review.sqlite3"
    reviewed_document(database)
    assert not _keyword_search(database, "theft", 6)
    with pytest.raises(ValueError, match="Confirm"):
        approve_document(database, "penal.pdf")
    publish(database)
    assert _keyword_search(database, "theft", 6)
    save_section_review(database, "penal-code-section-366", SectionReviewUpdate(
        text="Whoever dishonestly takes movable property commits theft.", status="needs_correction"))
    assert not _keyword_search(database, "theft", 6)
    with pytest.raises(ValueError, match="every extracted"):
        publish(database)


def test_metadata_cannot_bypass_approval(tmp_path):
    database = tmp_path / "review.sqlite3"
    reviewed_document(database)
    with pytest.raises(ValueError, match="Approve & publish"):
        save_document_metadata(database, "penal.pdf", DocumentMetadataUpdate(status="verified"))


@pytest.mark.parametrize("field,value", [("official_source_url", "not-a-url"), ("effective_date", "invalid"),
    ("publication_date", "2024-02-31"), ("effective_date", "2999-01-01")])
def test_publication_requires_valid_source_details(tmp_path, field, value):
    database = tmp_path / "review.sqlite3"
    reviewed_document(database)
    with connect(database) as connection:
        connection.execute(f"UPDATE document_metadata SET {field}=?", (value,))
    with pytest.raises(ValueError):
        publish(database)
    assert not _keyword_search(database, "theft", 6)


def test_unregistered_document_is_not_searchable(tmp_path):
    database = tmp_path / "review.sqlite3"
    seed(database)
    assert not _keyword_search(database, "theft", 6)


@pytest.mark.parametrize("value", [None, ""])
def test_publication_with_optional_source_details_omitted(tmp_path, value):
    database = tmp_path / "review.sqlite3"
    reviewed_document(database)
    with connect(database) as connection:
        connection.execute(
            "UPDATE document_metadata SET official_source_url=?, publication_date=?, effective_date=?",
            (value, value, value),
        )
    publish(database)
    assert list_documents(database)[0].status == "verified"
    assert _keyword_search(database, "theft", 6)
