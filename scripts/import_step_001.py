import json
from pathlib import Path

from app.admin import list_documents, save_document_metadata
from app.config import get_settings
from app.models import DocumentMetadataUpdate
from app.rag.extract import extract_pdf
from app.rag.store import replace_document_chunks


SOURCE_DIR = Path("step 001 pdf folder")
AUDIT_PATH = Path("data/imports/step_001_audit.json")

IMPORTS = {
    "11_Wages-Boards-Ordinance-No.27-of-1941.pdf": (
        "Wages Boards Ordinance",
        "Consolidated compilation; exact publisher URL and currency require review.",
    ),
    "25-2017_E.pdf": (
        "Electronic Transactions (Amendment) Act No. 25 of 2017",
        "Government Printing publication; certified 2 November 2017.",
    ),
    "6242.pdf": (
        "Personal Data Protection Act No. 9 of 2022",
        "Government Printing publication; certified 19 March 2022.",
    ),
    "6297.pdf": (
        "Assistance to and Protection of Victims of Crime and Witnesses Act No. 10 of 2023",
        "Government Printing publication; certified 8 August 2023.",
    ),
    "6311.pdf": (
        "Online Safety Act No. 9 of 2024",
        "Government Printing publication; certified 1 February 2024.",
    ),
    "Bail-Act-Consolidated-2024.pdf": (
        "Bail Act (Consolidated 2024)",
        "Consolidated text; exact publisher URL and currency require review.",
    ),
    "CAA_Act_E.pdf": (
        "Consumer Affairs Authority Act No. 9 of 2003",
        "Government Printing publication; certified 17 March 2003.",
    ),
    "ComputerCrimesActNo24of2007.pdf": (
        "Computer Crime Act No. 24 of 2007",
        "Government Printing publication; certified 9 July 2007.",
    ),
    "ElectronicTransactionActNo19of2006.pdf": (
        "Electronic Transactions Act No. 19 of 2006",
        "Government Printing publication; certified 19 May 2006.",
    ),
    "Evidence-Ordinance-Consolidated-2024.pdf": (
        "Evidence Ordinance (Consolidated 2024)",
        "Consolidated text through Act No. 6 of 2021; publisher URL requires review.",
    ),
    "Industrial-Disputes-Act-Consolidated-2024.pdf": (
        "Industrial Disputes Act (Consolidated 2024)",
        "Consolidated text through Act No. 24 of 2022; publisher URL requires review.",
    ),
    "Minimum_Wages_(Indian_Labour)_Ordinance.pdf": (
        "Minimum Wages (Indian Labour) Ordinance",
        "Historical consolidated text; current legal status requires verification.",
    ),
    "Prevention of Domestic Violence Act, No. 34 of 2005 - English.pdf": (
        "Prevention of Domestic Violence Act No. 34 of 2005",
        "Government Printing publication; certified 3 October 2005.",
    ),
    "Shop-and-Office-Employees-Consolidated-2024.pdf": (
        "Shop and Office Employees Act (Consolidated 2024)",
        "Consolidated text through Act No. 28 of 2024; publisher URL requires review.",
    ),
}

EXCLUDED = {
    "575-2025_E.pdf": (
        "excluded",
        "Personal Data Protection amendment bill, not an enacted Act.",
    ),
    "4c03ba2f2.pdf": (
        "excluded",
        "Duplicate Domestic Violence Act copy; retained the Government Printing edition.",
    ),
}


def main() -> None:
    settings = get_settings()
    existing = {
        item.source_file for item in list_documents(settings.legal_ai_database)
    }
    audit = []

    for filename, (law, note) in IMPORTS.items():
        path = SOURCE_DIR / filename
        if not path.exists():
            audit.append(
                {"file": filename, "status": "missing", "reason": "File not found."}
            )
            continue
        if filename in existing:
            audit.append(
                {"file": filename, "status": "already_imported", "law": law}
            )
            continue
        chunks = extract_pdf(path, law)
        if not chunks:
            audit.append(
                {
                    "file": filename,
                    "status": "needs_manual_review",
                    "reason": "No numbered sections extracted.",
                }
            )
            continue
        replace_document_chunks(
            settings.legal_ai_database,
            filename,
            chunks,
        )
        save_document_metadata(
            settings.legal_ai_database,
            filename,
            DocumentMetadataUpdate(
                status="unreviewed",
                notes=f"Imported from step 001 pdf folder. {note}",
            ),
        )
        audit.append(
            {
                "file": filename,
                "status": "imported",
                "law": law,
                "sections": len(chunks),
                "notes": note,
            }
        )

    for filename, (status, reason) in EXCLUDED.items():
        audit.append({"file": filename, "status": status, "reason": reason})

    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_PATH.write_text(
        json.dumps(audit, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(audit, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

