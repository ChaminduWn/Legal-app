import argparse
import json
from dataclasses import asdict
from pathlib import Path

from app.rag.extract import extract_pdf
from app.rag.store import replace_document_chunks, connect
from app.admin import initialize_admin, register_import
from app.config import get_settings
from app.storage import store_file, register_asset


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
    parser.add_argument("--database", type=Path, default=get_settings().legal_ai_database)
    parser.add_argument("--output", type=Path, help="Optional derived JSON export; not needed by the application")
    args = parser.parse_args()

    initialize_admin(args.database)
    chunks = []
    report = []
    for filename, law_name in DOCUMENTS.items():
        path = args.source / filename
        if not path.exists():
            report.append({"file": filename, "status": "missing", "chunks": 0})
            continue
        with connect(args.database) as connection:
            if connection.execute("SELECT 1 FROM legal_chunks WHERE source_file=?", (filename,)).fetchone():
                report.append({"file":filename,"status":"already_indexed; preserved","chunks":0})
                continue
        extracted = extract_pdf(path, law_name)
        if not extracted:
            report.append({"file":filename,"status":"needs_extraction_review","chunks":0})
            continue
        asset = store_file(get_settings().legal_ai_storage_root,path)
        with connect(args.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            replace_document_chunks(args.database,filename,extracted,connection=connection)
            register_import(args.database,filename,"other","pdf_text",None,[],extracted,connection=connection)
            register_asset(connection,filename,0,asset)
        chunks.extend(extracted)
        report.append({"file": filename, "status": "indexed", "chunks": len(extracted)})

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps([asdict(chunk) for chunk in chunks], indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"total_chunks": len(chunks), "documents": report}, indent=2))


if __name__ == "__main__":
    main()

