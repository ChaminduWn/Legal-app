import argparse
import json
import time
from pathlib import Path

import numpy as np

from app.config import get_settings
from app.rag.store import _embedding_model, connect, initialize


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=get_settings().legal_ai_database)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="Number of embedding workers. Use 1 for the lowest memory usage.",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        help="Stop after this many batches (useful for a performance check).",
    )
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Report current coverage without loading the embedding model.",
    )
    args = parser.parse_args()
    settings = get_settings()
    initialize(args.database)

    with connect(args.database) as connection:
        rows = connection.execute(
            """
            SELECT source_id, law, section, heading, text
            FROM legal_chunks
            ORDER BY source_id
            """
        ).fetchall()
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT source_id FROM legal_embeddings WHERE model = ?",
                (settings.legal_ai_embedding_model,),
            )
        }

    pending = [row for row in rows if row["source_id"] not in existing]
    print(
        json.dumps(
            {
                "model": settings.legal_ai_embedding_model,
                "embedded": len(existing),
                "total_sections": len(rows),
                "remaining": len(pending),
                "coverage_percent": round(
                    (len(existing) / len(rows) * 100) if rows else 100.0, 2
                ),
            },
            indent=2,
        ),
        flush=True,
    )
    if args.status_only or not pending:
        return

    model = _embedding_model()
    indexed = 0
    started_at = time.monotonic()
    for batch_number, start in enumerate(
        range(0, len(pending), args.batch_size), start=1
    ):
        if args.max_batches and batch_number > args.max_batches:
            break
        batch = pending[start : start + args.batch_size]
        passages = [
            (
                f"{row['law']}. Section {row['section']}. "
                f"{row['heading'] or ''}\n{row['text']}"
            )
            for row in batch
        ]
        vectors = list(
            model.passage_embed(
                passages,
                batch_size=args.batch_size,
                parallel=args.parallel if args.parallel > 1 else None,
            )
        )
        records = []
        for row, vector in zip(batch, vectors, strict=True):
            normalized = np.asarray(vector, dtype=np.float32)
            normalized = normalized / (np.linalg.norm(normalized) or 1.0)
            records.append(
                (
                    row["source_id"],
                    settings.legal_ai_embedding_model,
                    int(normalized.shape[0]),
                    normalized.tobytes(),
                )
            )
        with connect(args.database) as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO legal_embeddings
                (source_id, model, dimensions, vector)
                VALUES (?, ?, ?, ?)
                """,
                records,
            )
        indexed += len(records)
        elapsed = time.monotonic() - started_at
        rate = indexed / elapsed if elapsed else 0.0
        remaining_seconds = (len(pending) - indexed) / rate if rate else 0.0
        print(
            f"Indexed {indexed}/{len(pending)} new sections "
            f"({rate:.1f}/sec, ETA {remaining_seconds / 60:.1f} min)",
            flush=True,
        )

    total_current = len(existing) + indexed
    print(
        json.dumps(
            {
                "model": settings.legal_ai_embedding_model,
                "new_embeddings": indexed,
                "embedded": total_current,
                "total_sections": len(rows),
                "remaining": len(rows) - total_current,
                "coverage_percent": round(
                    (total_current / len(rows) * 100) if rows else 100.0, 2
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
