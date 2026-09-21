"""Postgres upsert layer — dedup happens here via the schema's natural PKs."""
import psycopg
from . import schema

def ensure_schema(dsn):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for ddl in schema.DDL:
            cur.execute(ddl)
        # DDL creates tables that are absent; migrations reshape ones that are
        # already there. Both are idempotent, so this runs every startup.
        for mig in schema.MIGRATIONS:
            cur.execute(mig)

def upsert(dsn, table_rows):
    """table_rows: {table: [row_tuple, ...]}. Returns total rows upserted."""
    total = 0
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for table, rows in table_rows.items():
            sql = schema.UPSERTS[table]
            for row in rows:
                cur.execute(sql, row)
            total += len(rows)
    return total

def log_run(dsn, source, status, rows, message=""):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO collect_runs (finished_at, source, status, rows_upserted, message)"
            " VALUES (now(), %s, %s, %s, %s)", (source, status, rows, message[:500]))

def last_success_age_hours(dsn, source=None):
    """Hours since the last successful run -- of any source, or of one source
    (used to pace the weekly URL Inspection sample off its own history)."""
    sql = ("SELECT EXTRACT(epoch FROM now() - max(finished_at))/3600"
           " FROM collect_runs WHERE status = 'ok'")
    params = ()
    if source:
        sql += " AND source = %s"
        params = (source,)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        v = cur.fetchone()[0]
        return float(v) if v is not None else None

def table_is_empty(dsn, table):
    """True when a table holds no rows -- the first-run signal that widens a
    source's window to backfill everything the upstream still retains."""
    if table not in schema.UPSERTS:
        raise ValueError(f"unknown table {table!r}")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(psycopg.sql.SQL("SELECT NOT EXISTS (SELECT 1 FROM {} LIMIT 1)")
                    .format(psycopg.sql.Identifier(table)))
        return bool(cur.fetchone()[0])
