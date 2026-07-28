from pathlib import Path

from app.rag.extract import extract_pdf


def test_penal_code_extracts_numbered_sections() -> None:
    chunks = extract_pdf(Path("Penal-Code-No-02-of-1883-E.pdf"), "Penal Code")
    assert len(chunks) > 100
    assert any(chunk.section == "314" for chunk in chunks)
    assert any(chunk.section == "366" for chunk in chunks)
    section_365c = next(chunk for chunk in chunks if chunk.section == "365C")
    assert "366. Whoever" not in section_365c.text
    assert all(chunk.page_start <= chunk.page_end for chunk in chunks)
