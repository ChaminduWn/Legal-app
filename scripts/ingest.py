import argparse
import json
from dataclasses import asdict
from pathlib import Path

from app.rag.extract import extract_pdf
from app.rag.store import replace_chunks


DOCUMENTS = {
    "Penal-Code-No-02-of-1883-E.pdf": "Penal Code",
    "Code-of-Criminal-Proceduer-Consolidated-2024.pdf": "Code of Criminal Procedure",
    "Motor-Traffic-Consolidated-2024.pdf": "Motor Traffic Act",
    "5719_0730729900.pdf": "Penal Code (Amendment) Act No. 16 of 2006",
    "6089_0734283200.pdf": "Penal Code (Amendment) Act No. 10 of 2018",
    "6205_0735527600.pdf": "Penal Code (Amendment) Act No. 6 of 2021",
    "6227_0737053200.pdf": "Penal Code (Amendment) Act No. 24 of 2021",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("."))
    parser.add_argument("--database", type=Path, default=Path("data/legal_index.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("data/processed/chunks.json"))
    args = parser.parse_args()

    chunks = []
    report = []
    for filename, law_name in DOCUMENTS.items():
        path = args.source / filename
        if not path.exists():
            report.append({"file": filename, "status": "missing", "chunks": 0})
            continue
        extracted = extract_pdf(path, law_name)
        chunks.extend(extracted)
        report.append({"file": filename, "status": "indexed", "chunks": len(extracted)})

    if not chunks:
        raise SystemExit("No legal sections were extracted.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps([asdict(chunk) for chunk in chunks], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    replace_chunks(args.database, chunks)
    print(json.dumps({"total_chunks": len(chunks), "documents": report}, indent=2))


if __name__ == "__main__":
    main()

