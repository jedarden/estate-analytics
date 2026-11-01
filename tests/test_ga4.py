import datetime as dt
from unittest import mock
from estate_analytics import schema
from estate_analytics.sources import ga4

TODAY = dt.date(2026, 9, 28)

def _row(dims, metrics):
    return {"dimensionValues": [{"value": v} for v in dims],
            "metricValues": [{"value": v} for v in metrics]}

def _fake(responses):
    """Serve canned runReport pages keyed by (dimension list, offset)."""
    calls = []
    def fake(url, headers, method=None, body=None):
        dims = tuple(d["name"] for d in body["dimensions"])
        calls.append((url, dims, body["offset"]))
        return responses[(dims, body["offset"])]
    return fake, calls

def test_rows_keyed_by_label_with_iso_dates():
    daily = tuple(ga4.DAILY_DIMS)
    pages = tuple(ga4.PAGE_DIMS)
    fake, calls = _fake({
        (daily, 0): {"rowCount": 1, "rows": [_row(
            ["20260927", "Organic Search", "google", "United States"],
            ["4", "3", "4", "2", "9", "212.5"])]},
        (pages, 0): {"rowCount": 1, "rows": [_row(
            ["20260927", "halfonadouble.com", "/stock/SPY"], ["5", "3", "140.0"])]},
    })
    with mock.patch.object(ga4, "get_json", fake):
        out = ga4.collect("{}", {"halfonadouble.com": "123"}, today=TODAY, tok="t")
    assert out["ga4_daily"] == [("halfonadouble.com", "2026-09-27", "Organic Search", "google",
                                 "United States", 4, 3, 4, 2, 9, 212.5)]
    assert out["ga4_pages_daily"] == [("halfonadouble.com", "2026-09-27", "halfonadouble.com",
                                       "/stock/SPY", 5, 3, 140.0)]
    assert all(url.endswith("/properties/123:runReport") for url, _, _ in calls)

def test_paginates_until_row_count():
    dims = tuple(ga4.PAGE_DIMS)
    r = _row(["20260927", "h", "/"], ["1", "1", "1"])
    fake, calls = _fake({
        (dims, 0): {"rowCount": 3, "rows": [r, r]},
        (dims, 2): {"rowCount": 3, "rows": [r]},
    })
    with mock.patch.object(ga4, "get_json", fake):
        rows = ga4.run_report("t", "123", ga4.PAGE_DIMS, ga4.PAGE_METRICS, TODAY, TODAY)
    assert len(rows) == 3
    assert [c[2] for c in calls] == [0, 2]

def test_empty_report_stops():
    """A property with no traffic returns no rows and no rowCount at all."""
    fake, calls = _fake({(tuple(ga4.PAGE_DIMS), 0): {}})
    with mock.patch.object(ga4, "get_json", fake):
        assert ga4.run_report("t", "1", ga4.PAGE_DIMS, ga4.PAGE_METRICS, TODAY, TODAY) == []
    assert len(calls) == 1

def test_row_width_matches_upsert():
    """collect() tuples must line up with the upsert's placeholders."""
    assert len(ga4.DAILY_DIMS) + len(ga4.DAILY_METRICS) + 1 == schema.UPSERTS["ga4_daily"].count("%s")
    assert len(ga4.PAGE_DIMS) + len(ga4.PAGE_METRICS) + 1 == schema.UPSERTS["ga4_pages_daily"].count("%s")

def test_token_requests_analytics_scope():
    seen = {}
    with mock.patch.object(ga4.gsc, "_token", lambda sa, scope: seen.setdefault("scope", scope) and "t"), \
         mock.patch.object(ga4, "get_json", lambda *a, **k: {}):
        ga4.collect('{"client_email": "x"}', {"s": "1"}, today=TODAY)
    assert seen["scope"] == ga4.SCOPE


# --- automation classifier ---------------------------------------------------
# Cells from halfonadouble.com (property 557214664), 2026-09-28 to 10-27, as
# (sessions, engaged_sessions) per (day, country, channel).

def test_singapore_bot_cells_are_flagged():
    for sessions, engaged in [(2885, 1), (1604, 2), (992, 0), (989, 0), (919, 0),
                              (472, 1), (312, 2), (92, 1), (72, 0), (15, 0), (11, 0)]:
        assert ga4.is_automated(sessions, engaged), (sessions, engaged)

def test_real_low_volume_cells_are_kept():
    # Canada / Organic Search runs ~4 sessions a day at ~60% engaged; the US
    # Direct cell peaks around 20 a day; small zero-engagement cells sit under
    # the floor and are noise, not evidence.
    for sessions, engaged in [(4, 2), (4, 3), (10, 8), (9, 9), (20, 11), (19, 4),
                              (10, 2), (6, 0), (9, 0), (1, 0)]:
        assert not ga4.is_automated(sessions, engaged), (sessions, engaged)

def test_rule_boundaries():
    assert ga4.AUTOMATION_MIN_SESSIONS == 10 and ga4.AUTOMATION_MAX_ENGAGED_PCT == 5
    assert ga4.is_automated(10, 0) and not ga4.is_automated(9, 0)
    assert ga4.is_automated(20, 1) and not ga4.is_automated(20, 2)
    assert ga4.is_automated(100, 5) and not ga4.is_automated(100, 6)

def _daily(day, channel, source, country, sessions, engaged, views=None):
    return ("halfonadouble.com", day, channel, source, country,
            sessions, engaged, sessions, sessions, views or sessions, 0.0)

FIXTURE = [
    # Singapore Direct split across two sources: 6 + 5 = 11 sessions, 0 engaged.
    _daily("2026-10-12", "Direct", "(direct)", "Singapore", 6, 0),
    _daily("2026-10-12", "Direct", "(not set)", "Singapore", 5, 0),
    # The burst day.
    _daily("2026-10-13", "Direct", "(direct)", "Singapore", 2885, 1, 2942),
    # Same country, different channel: classified on its own.
    _daily("2026-10-13", "Organic Search", "google", "Singapore", 3, 2),
    # One source looks idle, but the cell as a whole is engaged.
    _daily("2026-10-13", "Direct", "(direct)", "United States", 9, 0),
    _daily("2026-10-13", "Direct", "chatgpt.com", "United States", 6, 5),
    _daily("2026-10-13", "Organic Search", "google", "Canada", 4, 3, 12),
]

def test_cells_are_classified_on_totals_across_sources():
    assert ga4.automated_cells(FIXTURE) == {
        ("halfonadouble.com", "2026-10-12", "Singapore", "Direct"),
        ("halfonadouble.com", "2026-10-13", "Singapore", "Direct"),
    }

def _sqlite_with(rows):
    import sqlite3
    db = sqlite3.connect(":memory:")
    ga4_ddl = next(d for d in schema.DDL if "ga4_daily (" in d)
    db.execute(ga4_ddl)
    db.executemany(schema.UPSERTS["ga4_daily"].replace("%s", "?"), rows)
    return db

def test_sql_flags_exactly_the_cells_python_flags():
    """The published datasets classify in SQL and ga4-query.py in Python. The
    two must agree row for row, including cells split across sources."""
    db = _sqlite_with(FIXTURE)
    got = {(p, d, c, ch) for p, d, ch, _s, c, *_m, automated
           in db.execute(ga4.CLASSIFIED_DAILY_SQL) if automated}
    assert got == ga4.automated_cells(FIXTURE)
    # Raw rows are untouched: the classifier is a read-time view, not a delete.
    assert db.execute("SELECT COUNT(*), SUM(sessions) FROM ga4_daily").fetchone() == (7, 2918)

def test_sql_embeds_the_python_constants():
    sql = ga4.CLASSIFIED_DAILY_SQL
    assert f">= {ga4.AUTOMATION_MIN_SESSIONS}" in sql
    assert f"<= {ga4.AUTOMATION_MAX_ENGAGED_PCT} * SUM(sessions) OVER cell" in sql
    assert "PARTITION BY property, day, country, channel" in sql
