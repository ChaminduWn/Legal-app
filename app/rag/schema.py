from dataclasses import dataclass


@dataclass(frozen=True)
class LegalChunk:
    source_id: str
    law: str
    section: str
    heading: str | None
    text: str
    source_file: str
    page_start: int
    page_end: int

