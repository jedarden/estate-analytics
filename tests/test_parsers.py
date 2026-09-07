import datetime as dt, json
from unittest import mock
from estate_analytics.sources import github_traffic, cf_rum

TODAY = dt.date(2026, 8, 24)

def test_github_traffic_merges_views_and_clones_per_day():
    responses = {
        "/user/repos?affiliation=owner&per_page=100&page=1": [
            {"name": "NEEDLE", "owner": {"login": "jedarden"}, "private": False, "fork": False}],
        "/user/repos?affiliation=owner&per_page=100&page=2": [],
        "/repos/jedarden/NEEDLE/traffic/views?per=day": {
            "views": [{"timestamp": "2026-08-23T00:00:00Z", "count": 10, "uniques": 4}]},
        "/repos/jedarden/NEEDLE/traffic/clones?per=day": {
            "clones": [{"timestamp": "2026-08-23T00:00:00Z", "count": 3, "uniques": 2}]},
        "/repos/jedarden/NEEDLE/traffic/popular/referrers": [
            {"referrer": "chatgpt.com", "count": 18, "uniques": 2}],
        "/repos/jedarden/NEEDLE/traffic/popular/paths": [
            {"path": "/jedarden/NEEDLE", "title": "NEEDLE", "count": 9, "uniques": 5}],
    }
    def fake(url, *a, **k):
        return responses[url.replace(github_traffic.API, "")]
    with mock.patch.object(github_traffic, "get_json", fake):
        out = github_traffic.collect("t", "jedarden", today=TODAY)
    assert out["repo_traffic_daily"] == [("NEEDLE", "2026-08-23", 10, 4, 3, 2)]
    assert out["repo_referrers_daily"] == [(TODAY, "NEEDLE", "chatgpt.com", 18, 2)]
    assert out["repo_paths_daily"] == [(TODAY, "NEEDLE", "/jedarden/NEEDLE", "NEEDLE", 9, 5)]

def test_cf_rum_rows_and_null_referer():
    payload = {"data": {"viewer": {"accounts": [{"rumPageloadEventsAdaptiveGroups": [
        {"count": 7, "dimensions": {"date": "2026-08-23", "requestHost": "jedarden.com",
                                    "requestPath": "/", "refererHost": None}}]}]}}}
    with mock.patch.object(cf_rum, "get_json", lambda *a, **k: payload):
        out = cf_rum.collect("t", "acct", today=TODAY)
    assert out["cf_rum_daily"] == [("2026-08-23", "jedarden.com", "/", "", 7)]

def test_cf_rum_keeps_hosts_distinct():
    """The account serves several sites. Two homepages on the same day must stay
    two rows -- collapsing them is the bug this dimension exists to prevent."""
    payload = {"data": {"viewer": {"accounts": [{"rumPageloadEventsAdaptiveGroups": [
        {"count": 30, "dimensions": {"date": "2026-09-06", "requestHost": "jedarden.com",
                                     "requestPath": "/", "refererHost": None}},
        {"count": 31, "dimensions": {"date": "2026-09-06", "requestHost": "halfonadouble.com",
                                     "requestPath": "/", "refererHost": None}}]}]}}}
    with mock.patch.object(cf_rum, "get_json", lambda *a, **k: payload):
        out = cf_rum.collect("t", "acct", today=TODAY)
    rows = out["cf_rum_daily"]
    assert len(rows) == 2
    assert {r[1] for r in rows} == {"jedarden.com", "halfonadouble.com"}
    # Same (day, path, referer) triple -- only host separates them.
    assert len({(r[0], r[2], r[3]) for r in rows}) == 1

def test_cf_rum_requests_the_host_dimension():
    """A query missing requestHost makes Cloudflare pre-aggregate across sites,
    which no amount of downstream schema can undo."""
    captured = {}
    def fake(url, headers, method=None, body=None):
        captured["body"] = body
        return {"data": {"viewer": {"accounts": [{"rumPageloadEventsAdaptiveGroups": []}]}}}
    with mock.patch.object(cf_rum, "get_json", fake):
        cf_rum.collect("t", "acct", today=TODAY)
    assert "requestHost" in captured["body"]["query"]
