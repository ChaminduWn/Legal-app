import re
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


def _page_for_offset(offsets: list[tuple[int, int, int]], offset: int) -> int:
    for start, end, page_number in offsets:
        if start <= offset <= end:
            return page_number
    return offsets[-1][2]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
