"""Audit by default; --apply backs up, migrates, archives unused metadata and copies originals.
Never deletes source files or invents dates. Run from the repository root.
"""
import argparse
import json
import sqlite3
from datetime import datetime, UTC
from pathlib import Path

from app.admin import initialize_admin, resolve_pdf
from app.config import get_settings
from app.rag.store import connect
from app.storage import register_asset, store_file, resolve_asset


def audit(database: Path) -> dict:
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as c:
        c.row_factory = sqlite3.Row
        scalar = lambda sql: c.execute(sql).fetchone()[0]
        return {
            "integrity": scalar("PRAGMA integrity_check"),
            "foreign_key_violations": [list(r) for r in c.execute("PRAGMA foreign_key_check")],
            "sections": scalar("SELECT COUNT(*) FROM legal_chunks"),
            "indexed_documents": scalar("SELECT COUNT(DISTINCT source_file) FROM legal_chunks"),
            "embeddings": scalar("SELECT COUNT(*) FROM legal_embeddings"),
            "metadata_without_sections": [dict(r) for r in c.execute("SELECT m.* FROM document_metadata m WHERE NOT EXISTS (SELECT 1 FROM legal_chunks c WHERE c.source_file=m.source_file)")],
            "documents_without_metadata": [r[0] for r in c.execute("SELECT DISTINCT c.source_file FROM legal_chunks c LEFT JOIN document_metadata m USING(source_file) WHERE m.source_file IS NULL")],
            "fts_rows": scalar("SELECT COUNT(*) FROM legal_chunks_fts"),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--database", type=Path, default=get_settings().legal_ai_database)
    parser.add_argument("--storage-root", type=Path, default=get_settings().legal_ai_storage_root)
    parser.add_argument("--report", type=Path, default=Path("reports/library-audit.json"))
    args = parser.parse_args()
    report = {"before": audit(args.database), "mode": "apply" if args.apply else "audit"}
    workspace = Path(__file__).resolve().parent.parent
    if args.apply:
        backup = args.database.parent / "backups" / ("before-catalog-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + ".sqlite3")
        backup.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(args.database) as source, sqlite3.connect(backup) as destination:
            source.backup(destination)
        report["backup"] = str(backup.resolve())
        initialize_admin(args.database)
        with connect(args.database) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS archived_metadata (
                source_file TEXT PRIMARY KEY, record_json TEXT NOT NULL, reason TEXT NOT NULL,
                archived_at TEXT NOT NULL)""")
            # Preserve inventory-only rows in an explicit archive, not the active search catalog.
            for row in report['before']['metadata_without_sections']:
                c.execute("INSERT OR IGNORE INTO archived_metadata VALUES (?,?,?,?)",
                    (row['source_file'],json.dumps(row,ensure_ascii=False),'No extracted sections; original may still need ingestion',datetime.now(UTC).isoformat()))
                c.execute("DELETE FROM document_metadata WHERE source_file=?", (row['source_file'],))
            c.execute("""INSERT OR IGNORE INTO document_metadata(source_file,status,updated_at)
                SELECT DISTINCT source_file,'unreviewed',? FROM legal_chunks""", (datetime.now(UTC).isoformat(),))
            # Rebuild the derived keyword index from authoritative reviewed text.
            c.execute("DELETE FROM legal_chunks_fts")
            c.execute("INSERT INTO legal_chunks_fts SELECT source_id,law,section,heading,text FROM legal_chunks")
            documents = c.execute("SELECT * FROM document_records").fetchall()
        copied, missing = [], []
        for record in documents:
            name = record['source_file']
            if resolve_asset(args.database,args.storage_root,name):
                continue
            original = resolve_pdf(workspace,name)
            if not original:
                missing.append(name)
                continue
            asset = store_file(args.storage_root,original)
            with connect(args.database) as c:
                register_asset(c,name,0,asset)
            copied.append(name)
        report['originals_copied'] = copied
        report['originals_missing'] = missing
        report['after'] = audit(args.database)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({"report":str(args.report.resolve()),"mode":report['mode'],
        "sections":report.get('after',report['before'])['sections'],
        "metadata_without_sections":len(report.get('after',report['before'])['metadata_without_sections']),
        "originals_copied":len(report.get('originals_copied',[])),"originals_missing":len(report.get('originals_missing',[]))}))


if __name__ == '__main__':
    main()
