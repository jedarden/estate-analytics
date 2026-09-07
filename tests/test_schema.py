from estate_analytics import schema

def test_every_table_has_primary_key():
    for ddl in schema.DDL:
        assert "PRIMARY KEY" in ddl, ddl[:60]

def test_every_upsert_dedupes_on_conflict():
    for name, sql in schema.UPSERTS.items():
        assert "ON CONFLICT" in sql, name

def test_upsert_targets_exist_in_ddl():
    tables = [d.split("IF NOT EXISTS ")[1].split(" ")[0].strip() for d in schema.DDL]
    for name in schema.UPSERTS:
        assert name in tables


def test_upsert_placeholders_match_column_count():
    """Adding a column and forgetting a %s placeholder is the failure mode a
    schema change invites; psycopg would raise only at runtime, in production."""
    import re
    for name, sql in schema.UPSERTS.items():
        cols = re.search(r"INSERT INTO %s(?: AS \w+)?\s*\(([^)]*)\)" % name, sql)
        assert cols, name
        n_cols = len([c for c in cols.group(1).split(",") if c.strip()])
        # Count value expressions, not just %s -- a literal like now() is a
        # legitimate value (repo_traffic_daily sets updated_at that way).
        values = re.search(r"VALUES\s*\((.*?)\)\s*\n", sql, re.S)
        assert values, name
        n_vals = len([v for v in values.group(1).split(",") if v.strip()])
        assert n_cols == n_vals, f"{name}: {n_cols} columns vs {n_vals} values"

def test_migrations_are_idempotent_by_construction():
    for mig in schema.MIGRATIONS:
        assert ("IF NOT EXISTS" in mig) or ("IF NOT EXISTS" in mig.upper()), mig[:60]
