import re
from contextlib import nullcontext
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.config import get_settings
from app.db import connect, is_postgres
from app.catalog import initialize_catalog, migrate_chunk_foreign_key
from app.models import LegalSource
from app.services.language import retrieval_query
from app.rag.schema import LegalChunk


TOKEN = re.compile(r"[A-Za-z0-9\u0D80-\u0DFF]+")
STOP_WORDS = {
    "and", "are", "can", "did", "for", "from", "have", "how", "into",
    "me", "my", "of", "on", "someone", "the", "they", "to", "was",
    "what", "when", "with",
}
LEGAL_EXPANSIONS = {
    "attack": {"assault", "hurt", "criminal force"},
    "attacked": {"assault", "hurt", "criminal force"},
    "beat": {"assault", "hurt", "criminal force"},
    "beaten": {"assault", "hurt", "criminal force"},
    "hit": {"assault", "hurt", "criminal force", "bodily pain"},
    "injured": {"injury", "hurt", "bodily pain"},
    "accessed": {"unauthorised access", "computer", "lawful authority"},
    "hacked": {"unauthorised access", "computer", "lawful authority"},
    "unauthorised": {"without lawful authority", "access any computer"},
    "unauthorized": {"without lawful authority", "access any computer"},
    "defective": {"consumer", "goods", "quality", "standards", "complaints"},
    "domestic": {"aggrieved person", "application", "magistrate", "protection order"},
    "protection": {"aggrieved person", "application", "protection order"},
    "refund": {"consumer", "trader", "goods", "complaints"},
    "seller": {"consumer", "trader", "goods"},
    "stole": {"theft", "dishonestly", "movable property"},
    "stolen": {"theft", "dishonestly", "movable property"},
    "taken": {"theft", "dishonestly", "movable property"},
    "took": {"theft", "dishonestly", "movable property"},
    "threat": {"criminal intimidation", "alarm"},
    "threatened": {"criminal intimidation", "alarm"},
}


def initialize(database: Path) -> None:
    with connect(database) as connection:
        if is_postgres(connection):
            return
        initialize_catalog(connection)
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS legal_chunks (
                source_id TEXT PRIMARY KEY,
                law TEXT NOT NULL,
                section TEXT NOT NULL,
                heading TEXT,
                text TEXT NOT NULL,
                source_file TEXT NOT NULL,
                page_start INTEGER NOT NULL,
                page_end INTEGER NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS legal_chunks_fts USING fts5(
                source_id UNINDEXED,
                law,
                section,
                heading,
                text,
                tokenize='porter unicode61'
            );
            CREATE TABLE IF NOT EXISTS legal_embeddings (
                source_id TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL,
                FOREIGN KEY(source_id) REFERENCES legal_chunks(source_id)
            );
            CREATE TABLE IF NOT EXISTS document_metadata (
                source_file TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'unreviewed',
                publication_date TEXT, effective_date TEXT, official_source_url TEXT
            );
            """
        )

    with connect(database) as connection:
        if is_postgres(connection):
            return
        columns = {r[1] for r in connection.execute("PRAGMA table_info(document_metadata)")}
        for name in ("publication_date", "effective_date", "official_source_url"):
            if name not in columns:
                connection.execute(f"ALTER TABLE document_metadata ADD COLUMN {name} TEXT")
    migrate_chunk_foreign_key(database)
    with connect(database) as connection:
        if not is_postgres(connection):
            connection.execute("CREATE INDEX IF NOT EXISTS chunks_document ON legal_chunks(source_file)")


def replace_chunks(database: Path, chunks: list[LegalChunk]) -> None:
    initialize(database)
    with connect(database) as connection:
        if connection.execute("SELECT 1 FROM legal_chunks LIMIT 1").fetchone():
            raise ValueError("Refusing to reset a nonempty legal library. Import documents individually to preserve history and reviews.")
        connection.execute("DELETE FROM legal_embeddings")
        connection.execute("DELETE FROM section_reviews")
        connection.execute("DELETE FROM legal_chunks")
        if not is_postgres(connection):
            connection.execute("DELETE FROM legal_chunks_fts")
        connection.executemany(
            "INSERT INTO document_records(source_file,title) VALUES (?,?) ON CONFLICT(source_file) DO UPDATE SET title=excluded.title",
            [(c.source_file, c.law) for c in chunks]
        )
        connection.executemany(
            """
            INSERT INTO legal_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.source_id,
                    item.law,
                    item.section,
                    item.heading,
                    item.text,
                    item.source_file,
                    item.page_start,
                    item.page_end,
                )
                for item in chunks
            ],
        )
        if not is_postgres(connection):
            connection.executemany(
                "INSERT INTO legal_chunks_fts VALUES (?, ?, ?, ?, ?)",
                [
                    (item.source_id, item.law, item.section, item.heading, item.text)
                    for item in chunks
                ],
            )


def replace_document_chunks(
    database: Path, source_file: str, chunks: list[LegalChunk], *, connection=None
) -> None:
    if connection is None:
        initialize(database)
    with (nullcontext(connection) if connection is not None else connect(database)) as connection:
        existing_ids = [
            row[0]
            for row in connection.execute(
                "SELECT source_id FROM legal_chunks WHERE source_file = ?",
                (source_file,),
            )
        ]
        if existing_ids:
            connection.execute(
                "DELETE FROM section_reviews WHERE source_id IN (SELECT source_id FROM legal_chunks WHERE source_file=?)",
                (source_file,)
            )
            connection.execute("UPDATE document_metadata SET status='in_review' WHERE source_file=?", (source_file,))
            placeholders = ",".join("?" for _ in existing_ids)
            connection.execute(
                f"DELETE FROM legal_embeddings WHERE source_id IN ({placeholders})",
                existing_ids,
            )
            if not is_postgres(connection):
                connection.execute(
                    f"DELETE FROM legal_chunks_fts WHERE source_id IN ({placeholders})",
                    existing_ids,
                )
            connection.execute(
                f"DELETE FROM legal_chunks WHERE source_id IN ({placeholders})",
                existing_ids,
            )
        connection.executemany(
            "INSERT INTO document_records(source_file,title) VALUES (?,?) ON CONFLICT(source_file) DO UPDATE SET title=excluded.title",
            [(c.source_file, c.law) for c in chunks]
        )
        connection.executemany(
            """
            INSERT INTO legal_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.source_id,
                    item.law,
                    item.section,
                    item.heading,
                    item.text,
                    item.source_file,
                    item.page_start,
                    item.page_end,
                )
                for item in chunks
            ],
        )
        if not is_postgres(connection):
            connection.executemany(
                "INSERT INTO legal_chunks_fts VALUES (?, ?, ?, ?, ?)",
                [
                    (item.source_id, item.law, item.section, item.heading, item.text)
                    for item in chunks
                ],
            )


def _keyword_search(database: Path, query: str, limit: int, as_of: str | None = None) -> list[LegalSource]:
    settings = get_settings()
    if not settings.database_url and not database.exists():
        return []
    original_terms = [
        token.lower()
        for token in TOKEN.findall(query)
        if len(token) > 2 and token.lower() not in STOP_WORDS
    ]
    expanded_terms = set(original_terms)
    for term in original_terms:
        expanded_terms.update(LEGAL_EXPANSIONS.get(term, set()))
    terms = sorted(expanded_terms)
    if not terms:
        return []

    with connect(database) as connection:
        if is_postgres(connection):
            search_query = " ".join(terms[:24])
            rows = connection.execute(
                """
                SELECT c.*, v.edition_year, v.valid_from, v.valid_to, v.instrument_id, m.publication_date, m.effective_date,
                       ts_rank(c.search_vector, plainto_tsquery('simple', ?)) AS rank
                FROM legal_chunks c
                LEFT JOIN document_metadata m USING (source_file)
                LEFT JOIN document_versions v USING (source_file)
                WHERE c.search_vector @@ plainto_tsquery('simple', ?)
                  AND m.status = 'verified'
                  AND (? = 0 OR (v.source_file IS NOT NULL AND m.official_source_url IS NOT NULL
                       AND m.publication_date IS NOT NULL AND m.effective_date IS NOT NULL))
                  AND ((v.source_file IS NULL AND ?::text IS NULL) OR
                       (v.valid_from <= COALESCE(?::text, CURRENT_DATE::text) AND
                        (v.valid_to IS NULL OR v.valid_to > COALESCE(?::text, CURRENT_DATE::text))))
                ORDER BY rank DESC
                LIMIT ?
                """,
                (search_query, search_query, int(settings.environment == "production"), as_of, as_of, as_of, limit),
            ).fetchall()
        else:
            fts_query = " OR ".join(f'"{term}"' for term in terms[:24])
            rows = connection.execute(
                """
                SELECT c.*, v.edition_year, v.valid_from, v.valid_to, v.instrument_id, m.publication_date, m.effective_date, bm25(legal_chunks_fts, 0, 3, 2, 1, 1) AS rank
                FROM legal_chunks_fts
                JOIN legal_chunks c USING (source_id)
                LEFT JOIN document_metadata m USING (source_file)
                LEFT JOIN document_versions v USING (source_file)
                WHERE legal_chunks_fts MATCH ?
                  AND m.status = 'verified'
                  AND (? = 0 OR (v.source_file IS NOT NULL AND m.official_source_url IS NOT NULL
                       AND m.publication_date IS NOT NULL AND m.effective_date IS NOT NULL))
                  AND ((v.source_file IS NULL AND ? IS NULL) OR
                       (v.valid_from <= COALESCE(?, date('now')) AND
                        (v.valid_to IS NULL OR v.valid_to > COALESCE(?, date('now')))))
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, int(settings.environment == "production"), as_of, as_of, as_of, limit),
            ).fetchall()

    if not rows:
        return []
    return [
        LegalSource(
            source_id=row["source_id"],
            law=row["law"],
            section=row["section"],
            heading=row["heading"],
            text=row["text"],
            source_file=row["source_file"],
            page_start=row["page_start"],
            page_end=row["page_end"],
            edition_year=row["edition_year"],
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            instrument_id=row["instrument_id"],
            publication_date=row["publication_date"],
            effective_date=row["effective_date"],
            score=round(1.0 / (1.0 + index * 0.15), 4),
        )
        for index, row in enumerate(rows)
    ]


@lru_cache(maxsize=1)
def _embedding_model():
    from fastembed import TextEmbedding

    settings = get_settings()
    settings.legal_ai_embedding_cache.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(
        model_name=settings.legal_ai_embedding_model,
        cache_dir=str(settings.legal_ai_embedding_cache),
    )


def _semantic_search(database: Path, query: str, limit: int, as_of: str | None = None) -> list[LegalSource]:
    settings = get_settings()
    with connect(database) as connection:
        rows = connection.execute(
            """
            SELECT c.*, v.edition_year, v.valid_from, v.valid_to, v.instrument_id, m.publication_date, m.effective_date, e.vector, e.dimensions
            FROM legal_embeddings e
            JOIN legal_chunks c USING (source_id)
            LEFT JOIN document_metadata m USING (source_file)
            LEFT JOIN document_versions v USING (source_file)
            WHERE e.model = ? AND m.status = 'verified'
              AND (? = 0 OR (v.source_file IS NOT NULL AND m.official_source_url IS NOT NULL
                   AND m.publication_date IS NOT NULL AND m.effective_date IS NOT NULL))
              AND ((v.source_file IS NULL AND ? IS NULL) OR
                   (v.valid_from <= COALESCE(?, date('now')) AND
                    (v.valid_to IS NULL OR v.valid_to > COALESCE(?, date('now')))))
            """,
            (settings.legal_ai_embedding_model, int(settings.environment == "production"), as_of, as_of, as_of),
        ).fetchall()
    if not rows:
        return []

    query_vector = np.asarray(
        list(_embedding_model().query_embed([query]))[0], dtype=np.float32
    )
    query_vector = query_vector / (np.linalg.norm(query_vector) or 1.0)
    matrix = np.vstack(
        [
            np.frombuffer(row["vector"], dtype=np.float32, count=row["dimensions"])
            for row in rows
        ]
    )
    scores = matrix @ query_vector
    order = np.argsort(scores)[::-1][:limit]
    return [
        LegalSource(
            source_id=rows[index]["source_id"],
            law=rows[index]["law"],
            section=rows[index]["section"],
            heading=rows[index]["heading"],
            text=rows[index]["text"],
            source_file=rows[index]["source_file"],
            page_start=rows[index]["page_start"],
            page_end=rows[index]["page_end"],
            edition_year=rows[index]["edition_year"],
            valid_from=rows[index]["valid_from"],
            valid_to=rows[index]["valid_to"],
            instrument_id=rows[index]["instrument_id"],
            publication_date=rows[index]["publication_date"],
            effective_date=rows[index]["effective_date"],
            score=round(float(scores[index]), 4),
        )
        for index in order
    ]


def reciprocal_rank_fusion(
    ranked_lists: list[list[LegalSource]], weights: list[float] | None = None
) -> list[LegalSource]:
    weights = weights or [1.0] * len(ranked_lists)
    scores: dict[str, float] = {}
    sources: dict[str, LegalSource] = {}
    for ranked, weight in zip(ranked_lists, weights, strict=True):
        for rank, source in enumerate(ranked, start=1):
            scores[source.source_id] = scores.get(source.source_id, 0.0) + (
                weight / (60 + rank)
            )
            sources[source.source_id] = source
    ordered = sorted(scores, key=scores.get, reverse=True)
    best = scores[ordered[0]] if ordered else 1.0
    return [
        sources[source_id].model_copy(
            update={"score": round(scores[source_id] / best, 4)}
        )
        for source_id in ordered
    ]


def has_semantic_index(database: Path) -> bool:
    settings = get_settings()
    if not settings.database_url and not database.exists():
        return False
    initialize(database)
    model = settings.legal_ai_embedding_model
    try:
        with connect(database) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM legal_embeddings WHERE model = ?",
                (model,),
            ).fetchone()[0]
        return count > 0
    except Exception:
        return False


def retrieval_mode(database: Path) -> str:
    return "hybrid" if has_semantic_index(database) else "keyword"


def search(database: Path, query: str, limit: int = 6, as_of: str | None = None) -> list[LegalSource]:
    normalized_query = retrieval_query(query)
    keyword_results = _keyword_search(database, normalized_query, max(limit * 5, 30), as_of)
    if not has_semantic_index(database):
        return keyword_results[:limit]
    semantic_results = _semantic_search(database, normalized_query, max(limit * 5, 30), as_of)
    keyword_weight = 2.2 if normalized_query != query else 1.3
    semantic_weight = 0.7 if normalized_query != query else 1.0
    fused = reciprocal_rank_fusion(
        [keyword_results, semantic_results],
        weights=[keyword_weight, semantic_weight],
    )
    normalized_lower = normalized_query.lower()
    if (
        "computer" in normalized_lower
        and any(term in normalized_lower for term in ("unauthorised", "unauthorized"))
    ):
        fused.sort(
            key=lambda source: (
                not (
                    "Computer Crime Act" in source.law
                    and source.section == "3"
                ),
            )
        )
    return fused[:limit]
