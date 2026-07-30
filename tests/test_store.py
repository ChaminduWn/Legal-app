from pathlib import Path

from app.rag.schema import LegalChunk
from app.models import LegalSource
from app.rag.store import reciprocal_rank_fusion, replace_chunks, search


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


def test_keyword_search_matches_sinhala_legal_text(tmp_path: Path) -> None:
    database = tmp_path / "index.sqlite3"
    replace_chunks(
        database,
        [
            LegalChunk(
                source_id="sinhala-bail-page-1",
                law="ඇප නීතිය",
                section="Page 1",
                heading="ඇප ලබා දීම",
                text="ඇප ලබා දීමේදී අධිකරණය විසින් නීතිමය කොන්දේසි සලකා බලනු ලැබේ.",
                source_file="Criminal law/ඇප.pdf",
                page_start=1,
                page_end=1,
            )
        ],
    )
    results = search(database, "ඇප ලබා දීමේ කොන්දේසි", 5)
    assert results
    assert results[0].source_id == "sinhala-bail-page-1"


def test_rank_fusion_rewards_sources_found_by_both_retrievers() -> None:
    shared = LegalSource(
        source_id="shared",
        law="Penal Code",
        section="366",
        text="Theft",
        source_file="penal.pdf",
        page_start=1,
        page_end=1,
        score=1,
    )
    keyword_only = shared.model_copy(
        update={"source_id": "keyword", "section": "393"}
    )
    semantic_only = shared.model_copy(
        update={"source_id": "semantic", "section": "394"}
    )
    fused = reciprocal_rank_fusion(
        [[keyword_only, shared], [semantic_only, shared]], [1.0, 1.0]
    )
    assert fused[0].source_id == "shared"
