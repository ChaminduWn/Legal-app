from pathlib import Path

from app.rag.schema import LegalChunk
from app.rag.store import replace_chunks, search


def test_search_returns_matching_source(tmp_path: Path) -> None:
    database = tmp_path / "index.sqlite3"
    replace_chunks(
        database,
        [
            LegalChunk(
                source_id="penal-code-section-314",
                law="Penal Code",
                section="314",
                heading="Causing hurt",
                text="Whoever causes bodily pain, disease or infirmity causes hurt.",
                source_file="penal.pdf",
                page_start=10,
                page_end=10,
            )
        ],
    )
    results = search(database, "Someone caused me bodily pain", 5)
    assert results
    assert results[0].source_id == "penal-code-section-314"


def test_search_expands_plain_language_violence_terms(tmp_path: Path) -> None:
    database = tmp_path / "index.sqlite3"
    replace_chunks(
        database,
        [
            LegalChunk(
                source_id="penal-code-section-342",
                law="Penal Code",
                section="342",
                heading="Assault",
                text="A person who uses criminal force commits an assault.",
                source_file="penal.pdf",
                page_start=10,
                page_end=10,
            )
        ],
    )
    assert search(database, "My neighbour hit me", 5)


def test_search_expands_plain_language_theft_terms(tmp_path: Path) -> None:
    database = tmp_path / "index.sqlite3"
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
    results = search(database, "My phone was taken from my bag", 5)
    assert results
    assert results[0].section == "366"
