"""The panel's data contract. These guard the parts that fail silently:
JSON-unserialisable types from psycopg, and the referrer snapshot semantics.
"""
import datetime as dt, decimal, json
from estate_analytics import publish

class FakeCursor:
    def __init__(self, rows, cols): self._rows, self.description = rows, [type("D",(),{"name":c}) for c in cols]
    def execute(self, sql): pass
    def fetchall(self): return self._rows
    def __enter__(self): return self
    def __exit__(self, *a): return False

class FakeConn:
    def __init__(self, per_query): self.per_query, self.calls = per_query, []
    def cursor(self):
        name = list(publish.QUERIES)[len(self.calls)]
        self.calls.append(name)
        rows, cols = self.per_query.get(name, ([], ["x"]))
        return FakeCursor(rows, cols)

class FakeS3:
    def __init__(self): self.objects = {}
    def put_object(self, Bucket, Key, Body, **kw): self.objects[Key] = (Bucket, Body, kw)

def _conn():
    return FakeConn({
        "search": ([(dt.date(2026,9,8), 4, 158, decimal.Decimal("5.6"))],
                   ["day","clicks","impressions","avg_position"]),
        "traffic": ([(dt.date(2026,9,9), "jedarden.com", 30)], ["day","host","pageviews"]),
        "repo-traffic": ([(dt.date(2026,9,9), 100, 40, 3)], ["day","views","uniques","clones"]),
    })

def test_dates_and_decimals_survive_json():
    """psycopg returns date and Decimal; json.dumps raises on both."""
    ds = publish.collect_datasets(_conn())
    json.dumps(ds)  # must not raise
    assert ds["search"][0]["day"] == "2026-09-08"
    assert ds["search"][0]["avg_position"] == 5.6

def test_publish_writes_every_dataset_plus_meta():
    s3 = FakeS3()
    n = publish.publish(_conn(), s3, "dashboard-site", "estate-analytics")
    assert n == len(publish.QUERIES) + 1
    keys = set(s3.objects)
    assert "estate-analytics/data/meta.json" in keys
    for q in publish.QUERIES:
        assert f"estate-analytics/data/{q}.json" in keys

def test_publish_writes_under_the_data_prefix_only():
    """dashboard-site CI syncs the repo over the bucket but excludes */data/*.
    Anything written outside data/ would be deleted on the next site push."""
    s3 = FakeS3()
    publish.publish(_conn(), s3, "dashboard-site", "estate-analytics")
    assert all(k.startswith("estate-analytics/data/") for k in s3.objects)

def test_meta_reports_latest_day_per_source():
    meta = publish.build_meta(publish.collect_datasets(_conn()))
    assert meta["latest"]["search"] == "2026-09-08"
    assert meta["latest"]["traffic"] == "2026-09-09"
    assert meta["rows"]["search"] == 1

def test_referrer_query_is_snapshot_keyed_not_summed_across_days():
    """repo_referrers_daily is a trailing-14-day snapshot per day. Grouping by
    snapshot_day keeps each observation separate; a query that summed across
    days would multiply-count the same fortnight."""
    sql = publish.QUERIES["ai-referrals"]
    assert "GROUP BY snapshot_day, referrer" in sql
    assert "chatgpt.com" in sql and "perplexity.ai" in sql

def test_meta_is_json_serialisable():
    json.dumps(publish.build_meta(publish.collect_datasets(_conn())))
