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
