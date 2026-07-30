import argparse
import hashlib
import json
import re
from pathlib import Path

from pypdf import PdfReader

from app.admin import list_documents, save_document_metadata
from app.config import get_settings
from app.models import DocumentMetadataUpdate
from app.rag.schema import LegalChunk
from app.rag.store import replace_document_chunks


ROOTS = [Path("Criminal law"), Path("Civil Law"), Path("පනත්")]
AUDIT_PATH = Path("data/imports/sinhala_collections_audit.json")
SPACE = re.compile(r"[ \t]+")
MAX_CHUNK_CHARS = 4500
OVERLAP_CHARS = 300


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def source_name(path: Path) -> str:
    return path.as_posix()


def law_name(path: Path) -> str:
    stem = path.stem.strip()
    if path.parent in ROOTS:
        return stem
    category = re.sub(r"^\d+[.)]?\s*", "", path.parent.name).strip()
    return f"{category} — {stem}" if category and category not in stem else stem


def clean_text(text: str) -> str:
    text = text.encode("utf-8", errors="replace").decode("utf-8")
    lines = []
    for raw in text.replace("\x00", "").splitlines():
        line = SPACE.sub(" ", raw).strip()
        if not line or re.fullmatch(r"\d+", line):
            continue
        lines.append(line)
    return "\n".join(lines)


def text_score(text: str) -> int:
    return sum(character.isalnum() for character in text)


def sample_pdf(path: Path) -> dict:
    try:
        reader = PdfReader(str(path), strict=False)
        pages = len(reader.pages)
        sample = "\n".join(
            clean_text(reader.pages[index].extract_text() or "")
            for index in range(min(3, pages))
        )
        if text_score(sample) < 100:
            status = "needs_ocr"
            reason = "The first pages contain too little extractable text."
        else:
            status = "ready"
            reason = None
        return {
            "pages": pages,
            "sample_characters": len(sample),
            "status": status,
            "reason": reason,
        }
    except Exception as exc:
        return {
            "pages": 0,
            "sample_characters": 0,
            "status": "unreadable",
            "reason": f"{type(exc).__name__}: {str(exc)[:180]}",
        }


def split_page(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK_CHARS:
        return [text] if text_score(text) >= 60 else []
    chunks = []
    cursor = 0
    while cursor < len(text):
        end = min(len(text), cursor + MAX_CHUNK_CHARS)
        if end < len(text):
            paragraph_end = text.rfind("\n", cursor + 1000, end)
            if paragraph_end > cursor:
                end = paragraph_end
        chunk = text[cursor:end].strip()
        if text_score(chunk) >= 60:
            chunks.append(chunk)
        if end >= len(text):
            break
        cursor = max(cursor + 1, end - OVERLAP_CHARS)
    return chunks


def extract_page_chunks(path: Path) -> list[LegalChunk]:
    reader = PdfReader(str(path), strict=False)
    relative = source_name(path)
    document_id = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:16]
    law = law_name(path)
    chunks = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = clean_text(page.extract_text() or "")
        for part, body in enumerate(split_page(text), start=1):
            label = f"Page {page_number}" if part == 1 else f"Page {page_number}.{part}"
            first_line = body.splitlines()[0][:160] if body else None
            chunks.append(
                LegalChunk(
                    source_id=f"si-{document_id}-p{page_number}-{part}",
                    law=law,
                    section=label,
                    heading=first_line,
                    text=body,
                    source_file=relative,
                    page_start=page_number,
                    page_end=page_number,
                )
            )
    return chunks


def audit_files() -> list[dict]:
    records = []
    hashes = {}
    paths = sorted(
        (path for root in ROOTS for path in root.rglob("*.pdf")),
        key=lambda item: source_name(item).casefold(),
    )
    for index, path in enumerate(paths, start=1):
        digest = file_hash(path)
        record = {
            "file": source_name(path),
            "bytes": path.stat().st_size,
            "sha256": digest,
            **sample_pdf(path),
        }
        if digest in hashes:
            record["status"] = "duplicate"
            record["duplicate_of"] = hashes[digest]
            record["reason"] = "Exact duplicate PDF."
        else:
            hashes[digest] = source_name(path)
        records.append(record)
        if index % 50 == 0:
            print(f"Audited {index}/{len(paths)} PDFs", flush=True)
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_PATH.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return records


def import_ready(records: list[dict]) -> None:
    settings = get_settings()
    existing = {item.source_file for item in list_documents(settings.legal_ai_database)}
    imported = 0
    sections = 0
    for record in records:
        if record["status"] != "ready":
            continue
        source_file = record["file"]
        if source_file in existing:
            record["status"] = "already_imported"
            continue
        path = Path(source_file)
        try:
            chunks = extract_page_chunks(path)
        except Exception as exc:
            record["status"] = "import_error"
            record["reason"] = f"{type(exc).__name__}: {str(exc)[:180]}"
            continue
        if not chunks:
            record["status"] = "needs_ocr"
            record["reason"] = "No usable page text was extracted."
            continue
        replace_document_chunks(settings.legal_ai_database, source_file, chunks)
        save_document_metadata(
            settings.legal_ai_database,
            source_file,
            DocumentMetadataUpdate(
                status="unreviewed",
                notes=(
                    "Imported from the user-supplied Sinhala legal collection. "
                    "Page-level extraction; official status, currency and text quality "
                    "require human verification."
                ),
            ),
        )
        record["status"] = "imported"
        record["sections"] = len(chunks)
        imported += 1
        sections += len(chunks)
        if imported % 20 == 0:
            print(f"Imported {imported} documents ({sections} page chunks)", flush=True)
    AUDIT_PATH.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"imported_documents": imported, "new_sections": sections}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--from-audit", action="store_true")
    args = parser.parse_args()
    records = (
        json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
        if args.from_audit and AUDIT_PATH.exists()
        else audit_files()
    )
    counts = {}
    for record in records:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    print(json.dumps({"audit": counts, "total": len(records)}))
    if not args.audit_only:
        import_ready(records)


if __name__ == "__main__":
    main()
