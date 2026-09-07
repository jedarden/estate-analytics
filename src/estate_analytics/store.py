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

def last_success_age_hours(dsn):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT EXTRACT(epoch FROM now() - max(finished_at))/3600"
                    " FROM collect_runs WHERE status = 'ok'")
        v = cur.fetchone()[0]
        return float(v) if v is not None else None
