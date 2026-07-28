import argparse
import json
from pathlib import Path

import numpy as np

from app.config import get_settings
from app.rag.store import _embedding_model, connect, initialize


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/legal_index.sqlite3"))
    parser.add_argument("--batch-size", type=int, default=64)
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
    model = _embedding_model()
    indexed = 0
    for start in range(0, len(pending), args.batch_size):
        batch = pending[start : start + args.batch_size]
        passages = [
            (
                f"{row['law']}. Section {row['section']}. "
                f"{row['heading'] or ''}\n{row['text']}"
            )
            for row in batch
        ]
        vectors = list(model.passage_embed(passages, batch_size=args.batch_size))
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
        print(f"Indexed {indexed}/{len(pending)} new sections", flush=True)

    print(
        json.dumps(
            {
                "model": settings.legal_ai_embedding_model,
                "new_embeddings": indexed,
                "total_sections": len(rows),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

