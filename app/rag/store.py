import re
import sqlite3
from pathlib import Path

from app.models import LegalSource
from app.rag.schema import LegalChunk


TOKEN = re.compile(r"[A-Za-z0-9]+")
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
    "stole": {"theft", "dishonestly", "movable property"},
    "stolen": {"theft", "dishonestly", "movable property"},
    "taken": {"theft", "dishonestly", "movable property"},
    "took": {"theft", "dishonestly", "movable property"},
    "threat": {"criminal intimidation", "alarm"},
    "threatened": {"criminal intimidation", "alarm"},
}


def connect(database: Path) -> sqlite3.Connection:
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    return connection


def initialize(database: Path) -> None:
    with connect(database) as connection:
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
            """
        )


def replace_chunks(database: Path, chunks: list[LegalChunk]) -> None:
    initialize(database)
    with connect(database) as connection:
        connection.execute("DELETE FROM legal_chunks")
        connection.execute("DELETE FROM legal_chunks_fts")
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
        connection.executemany(
            "INSERT INTO legal_chunks_fts VALUES (?, ?, ?, ?, ?)",
            [
                (item.source_id, item.law, item.section, item.heading, item.text)
                for item in chunks
            ],
        )


def search(database: Path, query: str, limit: int = 6) -> list[LegalSource]:
    if not database.exists():
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
    fts_query = " OR ".join(f'"{term}"' for term in terms[:24])
    with connect(database) as connection:
        rows = connection.execute(
            """
            SELECT c.*, bm25(legal_chunks_fts, 0, 3, 2, 1, 1) AS rank
            FROM legal_chunks_fts
            JOIN legal_chunks c USING (source_id)
            WHERE legal_chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit),
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
            score=round(1.0 / (1.0 + index * 0.15), 4),
        )
        for index, row in enumerate(rows)
    ]
