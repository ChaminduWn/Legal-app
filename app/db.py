import re
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Any

from app.config import get_settings

try:
    import psycopg
    from pgvector.psycopg import register_vector
except ImportError:
    psycopg = None


class DBRow(dict):
    """Row wrapper compatible with both sqlite3.Row and dict_row."""
    __slots__ = ("_items", "_names")

    def __init__(self, values: tuple, names: list[str]):
        super().__init__()
        self._items = values
        self._names = names
        for name, val in zip(names, values):
            self[name] = val

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._items[key]
        return super().__getitem__(key)

    def keys(self):
        return self._names


def db_row_factory(cursor):
    desc = cursor.description
    if not desc:
        return lambda values: values
    names = [d.name for d in desc]
    def make_row(values):
        return DBRow(values, names)
    return make_row


_QMARK_RE = re.compile(r"\?")


def translate_sql(sql: str) -> str:
    """Translate SQLite query idioms to PostgreSQL."""
    sql = sql.replace("date('now')", "CURRENT_DATE::text")
    sql = sql.replace("DATE('now')", "CURRENT_DATE::text")
    sql = re.sub(r"\bCURRENT_DATE\b(?!::text)", "CURRENT_DATE::text", sql)
    sql = sql.replace("BEGIN IMMEDIATE", "BEGIN")
    sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO")
    if "sqlite_master" in sql:
        sql = re.sub(
            r"sqlite_master\s+WHERE\s+name\s*=\s*'([^']+)'",
            r"information_schema.tables WHERE table_name = '\1'",
            sql,
            flags=re.IGNORECASE
        )
    # Replace ? with %s
    sql = _QMARK_RE.sub("%s", sql)
    return sql


class PostgresCursorWrapper:
    def __init__(self, cursor, conn_wrapper):
        self._cursor = cursor
        self._conn_wrapper = conn_wrapper

    def __iter__(self):
        return iter(self._cursor)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size=None):
        return self._cursor.fetchmany(size)

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description


class PostgresConnectionWrapper:
    def __init__(self, pg_conn):
        self._conn = pg_conn
        self.is_postgres = True

    def execute(self, sql: str, params=None):
        clean = sql.strip()
        # Handle PRAGMA emulation
        if clean.upper().startswith("PRAGMA "):
            return self._handle_pragma(clean)

        pg_sql = translate_sql(sql)
        cur = self._conn.cursor()
        if params is not None:
            # Handle single parameter passed as scalar or tuple/list
            if not isinstance(params, (list, tuple, dict)):
                params = (params,)
            cur.execute(pg_sql, params)
        else:
            cur.execute(pg_sql)
        return PostgresCursorWrapper(cur, self)

    def executemany(self, sql: str, params_seq):
        pg_sql = translate_sql(sql)
        cur = self._conn.cursor()
        cur.executemany(pg_sql, params_seq)
        return PostgresCursorWrapper(cur, self)

    def executescript(self, script: str):
        pg_sql = translate_sql(script)
        cur = self._conn.cursor()
        cur.execute(pg_sql)
        return PostgresCursorWrapper(cur, self)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._conn.rollback()
        else:
            self._conn.commit()

    def _handle_pragma(self, pragma_stmt: str):
        # Emulate PRAGMA table_info(table_name)
        m = re.match(r"(?i)PRAGMA\s+table_info\((['\"]?)([a-zA-Z0-9_]+)\1\)", pragma_stmt)
        if m:
            table_name = m.group(2)
            cur = self._conn.cursor()
            cur.execute("""
                SELECT 
                    ordinal_position - 1 AS cid,
                    column_name AS name,
                    data_type AS type,
                    CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull,
                    column_default AS dflt_value,
                    0 AS pk
                FROM information_schema.columns
                WHERE table_name = %s
                ORDER BY ordinal_position
            """, (table_name,))
            return PostgresCursorWrapper(cur, self)

        # Emulate PRAGMA foreign_key_list(table_name)
        m_fk = re.match(r"(?i)PRAGMA\s+foreign_key_list\((['\"]?)([a-zA-Z0-9_]+)\1\)", pragma_stmt)
        if m_fk:
            table_name = m_fk.group(2)
            cur = self._conn.cursor()
            cur.execute("""
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = %s AND constraint_type = 'FOREIGN KEY'
            """, (table_name,))
            return PostgresCursorWrapper(cur, self)

        # For foreign_keys=ON, busy_timeout, etc., just return a dummy empty cursor
        class DummyCursor:
            def fetchone(self): return None
            def fetchall(self): return []
            def __iter__(self): return iter([])
        return DummyCursor()


def _connect_postgres(database_url: str):
    try:
        conn = psycopg.connect(
            database_url,
            autocommit=False,
            row_factory=db_row_factory,
            connect_timeout=15,
        )
        return PostgresConnectionWrapper(conn)
    except Exception as direct_err:
        parsed = urllib.parse.urlparse(database_url)
        host = parsed.hostname or ""
        if host.startswith("db.") and host.endswith(".supabase.co"):
            project_ref = host.split(".")[1]
            password = urllib.parse.unquote(parsed.password or "")
            db = parsed.path.lstrip("/") or "postgres"
            pooler_host = "aws-0-ap-southeast-1.pooler.supabase.com"
            pooler_user = f"postgres.{project_ref}"
            conn = psycopg.connect(
                host=pooler_host,
                port=5432,
                user=pooler_user,
                password=password,
                dbname=db,
                autocommit=False,
                row_factory=db_row_factory,
                connect_timeout=15,
            )
            return PostgresConnectionWrapper(conn)
        raise direct_err


def connect(database: Path | str | None = None):
    """
    Connect to PostgreSQL (Supabase) if database_url is provided,
    otherwise fallback to SQLite database file.
    """
    settings = get_settings()
    db_url = settings.database_url

    # If an explicit postgres URL is passed
    if isinstance(database, str) and database.startswith("postgres"):
        db_url = database

    if db_url and psycopg is not None:
        return _connect_postgres(db_url)

    # SQLite fallback
    import sqlite3
    db_path = database if isinstance(database, Path) else settings.legal_ai_database
    if isinstance(database, str) and not database.startswith("postgres"):
        db_path = Path(database)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def is_postgres(connection) -> bool:
    return getattr(connection, "is_postgres", False)
