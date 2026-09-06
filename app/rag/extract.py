import re
import subprocess
import tempfile
from pathlib import Path

from pypdf import PdfReader

from app.rag.schema import LegalChunk


SECTION_START = re.compile(
    r"(?m)^(?P<section>\d{1,3}[A-Z]?(?:\s*[A-Z])?)\.\s*(?P<body>\S.*)$"
)
INLINE_SECTION_START = re.compile(
    r"""(?mx)
    ^[^\n\d]{1,120}?
    (?P<section>\d{1,3}[A-Z]?)\.?\s*
    (?P<body>
        (?:Whoever|Wherever|Where|When|If|Any|Every|No\s+person|The|A\s+person|
        Nothing|Notwithstanding|It\s+shall|Subject\s+to)\b.*
    )$
    """
)
SPACE = re.compile(r"[ \t]+")


def _clean_page(text: str) -> str:
    lines = []
    for raw_line in text.replace("\x00", "").splitlines():
        line = SPACE.sub(" ", raw_line).strip()
        if not line:
            continue
        if re.fullmatch(r"\d+", line):
            continue
        lines.append(line)
    return "\n".join(lines)


def extract_pdf(path: Path, law_name: str) -> list[LegalChunk]:
    reader = PdfReader(str(path))
    pages = [_clean_page(page.extract_text() or "") for page in reader.pages]
    joined_parts: list[str] = []
    offsets: list[tuple[int, int, int]] = []
    cursor = 0
    for page_number, page_text in enumerate(pages, start=1):
        joined_parts.append(page_text)
        end = cursor + len(page_text)
        offsets.append((cursor, end, page_number))
        cursor = end + 1
    text = "\n".join(joined_parts)
    candidates = list(SECTION_START.finditer(text)) + list(
        INLINE_SECTION_START.finditer(text)
    )
    matches = []
    seen_section_offsets: set[int] = set()
    for match in sorted(candidates, key=lambda item: item.start("section")):
        section_offset = match.start("section")
        if any(abs(section_offset - seen) < 4 for seen in seen_section_offsets):
            continue
        seen_section_offsets.add(section_offset)
        matches.append(match)
    chunks: list[LegalChunk] = []
    occurrences: dict[str, int] = {}

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.start():end].strip()
        if len(body) < 40:
            continue
        section = SPACE.sub("", match.group("section"))
        occurrences[section] = occurrences.get(section, 0) + 1
        page_start = _page_for_offset(offsets, match.start("section"))
        page_end = _page_for_offset(offsets, max(match.start("section"), end - 1))
        first_line = match.group("body").strip()
        heading = first_line if len(first_line) <= 160 else None
        source_id = (
            f"{_slug(path.stem)}-section-{section.lower()}"
            f"-{occurrences[section]}"
        )
        chunks.append(
            LegalChunk(
                source_id=source_id,
                law=law_name,
                section=section,
                heading=heading,
                text=body,
                source_file=path.name,
                page_start=page_start,
                page_end=page_end,
            )
        )
    return chunks


def chunks_from_pages(pages: list[str], source_name: str, law_name: str) -> list[LegalChunk]:
    """Extract numbered provisions from OCR pages, with a safe page fallback."""
    cleaned = [_clean_page(page) for page in pages]
    joined = "\n".join(cleaned)
    # Reuse the mature PDF parser by applying the same section rules here.
    offsets, cursor = [], 0
    for number, page in enumerate(cleaned, 1):
        offsets.append((cursor, cursor + len(page), number))
        cursor += len(page) + 1
    candidates = sorted(
        list(SECTION_START.finditer(joined)) + list(INLINE_SECTION_START.finditer(joined)),
        key=lambda item: item.start("section"),
    )
    matches, seen = [], set()
    for match in candidates:
        offset = match.start("section")
        if not any(abs(offset - old) < 4 for old in seen):
            seen.add(offset); matches.append(match)
    chunks, occurrences = [], {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(joined)
        body = joined[match.start():end].strip()
        if len(body) < 40:
            continue
        section = SPACE.sub("", match.group("section"))
        occurrences[section] = occurrences.get(section, 0) + 1
        chunks.append(LegalChunk(
            source_id=f"{_slug(Path(source_name).stem)}-section-{section.lower()}-{occurrences[section]}",
            law=law_name, section=section,
            heading=match.group("body").strip()[:160], text=body,
            source_file=source_name,
            page_start=_page_for_offset(offsets, match.start("section")),
            page_end=_page_for_offset(offsets, max(match.start("section"), end - 1)),
        ))
    if not chunks:
        chunks = [LegalChunk(
            source_id=f"{_slug(Path(source_name).stem)}-page-{i}", law=law_name,
            section=f"page-{i}", heading=f"Scanned page {i}", text=page,
            source_file=source_name, page_start=i, page_end=i,
        ) for i, page in enumerate(cleaned, 1) if len(page) >= 20]
    return chunks


def extract_images(paths: list[Path], law_name: str, tessdata: Path) -> tuple[list[LegalChunk], float, list[str]]:
    pages, confidences = [], []
    languages = "eng+sin"
    for path in paths:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "ocr"
            command = ["tesseract", str(path), str(output), "-l", languages,
                       "--tessdata-dir", str(tessdata), "--psm", "6", "tsv"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise ValueError("OCR could not read one of the images.")
            rows = (output.with_suffix(".tsv")).read_text(encoding="utf-8", errors="ignore").splitlines()
            words = []
            for row in rows[1:]:
                fields = row.split("\t", 11)
                if len(fields) == 12 and fields[11].strip():
                    words.append(fields[11].strip())
                    try:
                        score = float(fields[10])
                        if score >= 0: confidences.append(score)
                    except ValueError: pass
            pages.append(" ".join(words))
    confidence = round((sum(confidences) / len(confidences) / 100), 3) if confidences else 0.0
    warnings = [] if confidence >= .8 else ["OCR confidence is below 80%; compare every provision with the original image."]
    return chunks_from_pages(pages, paths[0].name, law_name), confidence, warnings


def _page_for_offset(offsets: list[tuple[int, int, int]], offset: int) -> int:
    for start, end, page_number in offsets:
        if start <= offset <= end:
            return page_number
    return offsets[-1][2]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
