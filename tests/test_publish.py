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
        "search": ([("sc-domain:devimprint.com", dt.date(2026,9,8), 4, 158, decimal.Decimal("5.6"))],
                   ["site","day","clicks","impressions","avg_position"]),
        "traffic": ([(dt.date(2026,9,9), "jedarden.com", 30)], ["day","host","pageviews"]),
        "repo-traffic": ([(dt.date(2026,9,9), 100, 40, 3)], ["day","views","uniques","clones"]),
    })

def test_dates_and_decimals_survive_json():
    """psycopg returns date and Decimal; json.dumps raises on both."""
    ds = publish.collect_datasets(_conn())
    json.dumps(ds)  # must not raise
    assert ds["search"][0]["day"] == "2026-09-08"
    assert ds["search"][0]["site"] == "sc-domain:devimprint.com"
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


def test_gate_datasets_are_published_with_their_thresholds():
    """The gate numbers come from the halfonadouble.com growth audit s3.1. If a
    threshold moves there, it must move here -- the panel compares against these."""
    for name in ("ga4-gates", "index-gate", "striking-distance"):
        assert name in publish.QUERIES
    assert "1000  AS journey_sessions_gate" in publish.QUERIES["ga4-gates"]
    assert "25000 AS raptive_pageviews_gate" in publish.QUERIES["ga4-gates"]
    assert "80 AS target_pct" in publish.QUERIES["index-gate"]
    # Head terms at position 70+ need authority, not tweaks: keep them out.
    assert "BETWEEN 8 AND 20" in publish.QUERIES["striking-distance"]


# --- GA4 automation filter ----------------------------------------------------
import re, sqlite3
from estate_analytics import schema
from estate_analytics.sources import ga4

GA4_DATASETS = ("ga4-gates", "ga4-traffic", "ga4-countries", "ga4-automation")

def _ga4_db():
    """ga4_daily in sqlite with a bot burst and real readers inside the window,
    plus an old bot row outside the 30-day gate window."""
    day = lambda n: (dt.date.today() - dt.timedelta(days=n)).isoformat()
    rows = [
        ("hoad", day(3), "Direct", "(direct)", "Singapore", 2885, 1, 2800, 2800, 2942, 3.0),
        ("hoad", day(3), "Direct", "(direct)", "United States", 20, 11, 15, 10, 52, 900.0),
        ("hoad", day(3), "Organic Search", "google", "Canada", 4, 3, 4, 3, 12, 300.0),
        ("hoad", day(3), "Direct", "(direct)", "Brazil", 6, 0, 6, 6, 6, 0.0),
        ("hoad", day(2), "Direct", "(direct)", "United States", 30, 1, 30, 30, 30, 1.0),
        ("hoad", day(60), "Direct", "(direct)", "Singapore", 500, 0, 500, 500, 500, 0.0),
    ]
    db = sqlite3.connect(":memory:")
    db.execute(next(d for d in schema.DDL if "ga4_daily (" in d))
    db.executemany(schema.UPSERTS["ga4_daily"].replace("%s", "?"), rows)
    return db

def _run(db, name):
    """Run a published GA4 query in sqlite. Only the date arithmetic and the
    numeric cast are Postgres-specific; the windows and FILTERs run as-is."""
    sql = re.sub(r"CURRENT_DATE - INTERVAL '(\d+) days'", r"date('now', '-\1 days')",
                 publish.QUERIES[name]).replace("::numeric", "")
    cur = db.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]

def test_every_ga4_session_dataset_reads_the_classified_rows():
    for name in GA4_DATASETS:
        assert ga4.CLASSIFIED_DAILY_SQL in publish.QUERIES[name], name
        assert "FROM ga4_daily\n" not in publish.QUERIES[name].replace(ga4.CLASSIFIED_DAILY_SQL, ""), name

def test_gates_use_figures_excluding_automation():
    (g,) = _run(_ga4_db(), "ga4-gates")
    # Flagged: the Singapore burst (2885 / 1 engaged) and the US cell on day 2
    # (30 / 1 engaged). Brazil (6 / 0) is under the session floor and is kept.
    assert g["automated_sessions"] == 2885 + 30
    assert g["sessions_excl_automation"] == 20 + 4 + 6
    assert g["page_views_excl_automation"] == 52 + 12 + 6
    assert g["tier1_sessions_excl_automation"] == 20 + 4
    assert g["engaged_sessions"] == 1 + 11 + 3 + 0 + 1
    # Raw totals stay alongside, for comparison; the 60-day-old row is outside.
    assert g["raw_sessions"] == 2885 + 20 + 4 + 6 + 30
    assert g["raw_page_views"] == 2942 + 52 + 12 + 6 + 30
    assert g["journey_sessions_gate"] == 1000 and g["raptive_pageviews_gate"] == 25000

def test_ga4_datasets_lead_with_engaged_and_never_publish_bare_sessions():
    db = _ga4_db()
    for name in ("ga4-gates", "ga4-traffic", "ga4-countries"):
        rows = _run(db, name)
        assert rows, name
        for r in rows:
            assert {"engaged_sessions", "sessions_excl_automation", "automated_sessions",
                    "raw_sessions"} <= set(r), name
            assert "sessions" not in r and "page_views" not in r, name
    countries = [r["country"] for r in _run(db, "ga4-countries")]
    # Ranked by engaged sessions: Singapore's 2,885 raw sessions do not lead.
    assert countries[0] == "United States" and countries.index("Singapore") > countries.index("Canada")

def test_automation_dataset_lists_each_flagged_cell():
    cells = _run(_ga4_db(), "ga4-automation")
    assert [(r["country"], r["channel"], r["sessions"]) for r in cells] == [
        ("Singapore", "Direct", 500), ("Singapore", "Direct", 2885), ("United States", "Direct", 30)]

def test_meta_publishes_the_automation_rule():
    meta = publish.build_meta(publish.collect_datasets(_conn()))
    assert meta["ga4_automation_rule"] == {
        "cell": ["property", "day", "country", "channel"],
        "min_sessions": ga4.AUTOMATION_MIN_SESSIONS,
        "max_engaged_pct": ga4.AUTOMATION_MAX_ENGAGED_PCT}
