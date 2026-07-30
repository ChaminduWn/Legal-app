import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.admin import list_documents, save_document_metadata
from app.config import get_settings
from app.models import DocumentMetadataUpdate
from app.rag.schema import LegalChunk
from app.rag.store import replace_document_chunks
from scripts.import_sinhala_collections import (
    AUDIT_PATH,
    clean_text,
    law_name,
    source_name,
    split_page,
    text_score,
)


REPORT_PATH = Path("data/imports/sinhala_ocr_report.json")
TESSDATA_DIR = Path("data/ocr/tessdata").resolve()
TEMP_ROOT = Path("tmp/pdfs/sinhala_ocr")


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def numeric_page(path: Path) -> int:
    match = re.search(r"-(\d+)\.(?:jpg|jpeg|png)$", path.name, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def render_pdf(path: Path, output_dir: Path, dpi: int) -> list[Path]:
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise RuntimeError("pdftoppm is not available.")
    if pdftoppm.lower().endswith((".cmd", ".bat")):
        native = (
            Path(pdftoppm).parents[2]
            / "native"
            / "poppler"
            / "Library"
            / "bin"
            / "pdftoppm.exe"
        )
        if native.exists():
            pdftoppm = str(native)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_dir / "page"
    subprocess.run(
        [
            pdftoppm,
            "-r",
            str(dpi),
            "-jpeg",
            "-jpegopt",
            "quality=88",
            str(path),
            str(prefix),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=3600,
    )
    return sorted(output_dir.glob("page-*.jpg"), key=numeric_page)


def ocr_image(image: Path) -> tuple[int, str]:
    tesseract = shutil.which("tesseract")
    if not tesseract:
        raise RuntimeError("Tesseract is not available.")
    result = subprocess.run(
        [
            tesseract,
            str(image),
            "stdout",
            "--tessdata-dir",
            str(TESSDATA_DIR),
            "-l",
            "sin+eng",
            "--psm",
            "3",
        ],
        check=True,
        capture_output=True,
        timeout=240,
    )
    return numeric_page(image), clean_text(
        result.stdout.decode("utf-8", errors="replace")
    )


def ocr_document(path: Path, workers: int, dpi: int) -> tuple[list[LegalChunk], int]:
    relative = source_name(path)
    document_id = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:16]
    work_dir = TEMP_ROOT / document_id
    shutil.rmtree(work_dir, ignore_errors=True)
    try:
        images = render_pdf(path, work_dir, dpi)
        if not images:
            raise RuntimeError("No page images were rendered.")
        page_text = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(ocr_image, image): image for image in images}
            for future in as_completed(futures):
                page_number, text = future.result()
                page_text[page_number] = text

        chunks = []
        law = law_name(path)
        for page_number in sorted(page_text):
            text = page_text[page_number]
            for part, body in enumerate(split_page(text), start=1):
                label = (
                    f"Page {page_number}"
                    if part == 1
                    else f"Page {page_number}.{part}"
                )
                chunks.append(
                    LegalChunk(
                        source_id=f"ocr-{document_id}-p{page_number}-{part}",
                        law=law,
                        section=label,
                        heading=body.splitlines()[0][:160] if body else None,
                        text=body,
                        source_file=relative,
                        page_start=page_number,
                        page_end=page_number,
                    )
                )
        return chunks, sum(text_score(text) for text in page_text.values())
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=max(1, min(4, os.cpu_count() // 2)))
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    if not (TESSDATA_DIR / "sin.traineddata").exists():
        raise SystemExit("Sinhala Tesseract model is missing.")

    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    candidates = [
        record
        for record in audit
        if record["status"] in {"needs_ocr", "ocr_failed"}
    ]
    candidates.sort(key=lambda record: (record.get("pages", 0), record["file"]))
    if args.limit:
        candidates = candidates[: args.limit]

    prior = {}
    if REPORT_PATH.exists():
        prior = {
            record["file"]: record
            for record in json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        }
    settings = get_settings()
    existing = {
        item.source_file for item in list_documents(settings.legal_ai_database)
    }

    completed = 0
    imported_chunks = 0
    for index, record in enumerate(candidates, start=1):
        source_file = record["file"]
        if source_file in existing:
            record["status"] = "ocr_imported"
            record["reason"] = "Document already exists in the index."
            continue
        result = {
            "file": source_file,
            "pages": record.get("pages", 0),
            "status": "ocr_running",
        }
        prior[source_file] = result
        save_json(REPORT_PATH, list(prior.values()))
        try:
            chunks, characters = ocr_document(
                Path(source_file), args.workers, args.dpi
            )
            if not chunks or characters < 200:
                raise ValueError("OCR produced too little usable text.")
            replace_document_chunks(
                settings.legal_ai_database,
                source_file,
                chunks,
            )
            save_document_metadata(
                settings.legal_ai_database,
                source_file,
                DocumentMetadataUpdate(
                    status="unreviewed",
                    notes=(
                        "OCR-imported from the user-supplied Sinhala legal collection. "
                        "OCR text, official status, currency and legal accuracy require "
                        "human verification against the PDF images."
                    ),
                ),
            )
            result.update(
                {
                    "status": "ocr_imported",
                    "chunks": len(chunks),
                    "recognized_characters": characters,
                }
            )
            record["status"] = "ocr_imported"
            record["reason"] = "Recovered with Sinhala and English OCR."
            record["sections"] = len(chunks)
            existing.add(source_file)
            completed += 1
            imported_chunks += len(chunks)
        except Exception as exc:
            result.update(
                {
                    "status": "ocr_failed",
                    "reason": f"{type(exc).__name__}: {str(exc)[:300]}",
                }
            )
            record["status"] = "ocr_failed"
            record["reason"] = result["reason"]
        finally:
            prior[source_file] = result
            save_json(REPORT_PATH, list(prior.values()))
            save_json(AUDIT_PATH, audit)
        print(
            f"OCR {index}/{len(candidates)}: {result['status']} - "
            f"{source_file.encode('unicode_escape').decode()}",
            flush=True,
        )

    print(
        json.dumps(
            {
                "ocr_imported_documents": completed,
                "new_chunks": imported_chunks,
                "processed_candidates": len(candidates),
            }
        )
    )


if __name__ == "__main__":
    main()
