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
