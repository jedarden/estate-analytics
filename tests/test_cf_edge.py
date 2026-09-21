"""Edge source: the eyeball filter, the 'other' derivation, path classes and the
Pages Functions project mapping. All credential-free -- get_json is faked."""
import datetime as dt
from unittest import mock
from estate_analytics.sources import cf_edge

TODAY = dt.date(2026, 9, 20)
ZONES = {"halfonadouble.com": "f267"}

def _group(day, status, count):
    return {"count": count, "dimensions": {"date": day, "edgeResponseStatus": status}}

def _crawler_payload(per_alias):
    return {"data": {"viewer": {"zones": [per_alias]}}}

def test_every_query_is_eyeball_only_and_host_scoped():
    """Without requestSource:eyeball the dataset includes Cloudflare's internal
    Cache API lookups, whose misses are logged as 504s under the client's host
    and UA. That is the bug this source exists to not have."""
    bodies = []
    def fake(url, headers, method=None, body=None, timeout=None):
        bodies.append(body)
        return {"data": {"viewer": {"zones": [{}]}}}
    with mock.patch.object(cf_edge, "get_json", fake):
        cf_edge.collect("t", ZONES, today=TODAY)
    assert bodies, "no queries issued"
    for b in bodies:
        assert 'requestSource: "eyeball"' in b["query"]
        assert "clientRequestHTTPHost: $host" in b["query"]
        assert b["variables"]["host"] == "halfonadouble.com"
        assert b["variables"]["zone"] == "f267"
        assert b["variables"]["since"] == "2026-09-17T00:00:00Z"

def test_other_is_all_minus_named_minus_empty_per_cell():
    per_alias = {
        "all": [_group("2026-09-19", 200, 100), _group("2026-09-19", 404, 10)],
        "empty": [_group("2026-09-19", 200, 5)],
        "googlebot_0": [_group("2026-09-19", 200, 30), _group("2026-09-19", 404, 10)],
        "seo_tools_0": [_group("2026-09-19", 200, 7)],
        "seo_tools_2": [_group("2026-09-19", 200, 3)],
    }
    calls = {"n": 0}
    def fake(url, headers, method=None, body=None, timeout=None):
        calls["n"] += 1
        # Every batch sees the same alias map; unknown aliases are simply absent.
        return _crawler_payload(per_alias) if "httpRequestsAdaptiveGroups(limit: 2000" in body["query"] \
            else {"data": {"viewer": {"zones": [{"httpRequestsAdaptiveGroups": []}]}}}
    with mock.patch.object(cf_edge, "get_json", fake):
        out = cf_edge.collect("t", ZONES, today=TODAY, batch_size=100)
    rows = {(r[2], r[3]): r[4] for r in out["cf_edge_daily"]}
    assert rows[("googlebot", "2xx")] == 30
    assert rows[("googlebot", "4xx")] == 10
    assert rows[("seo-tools", "2xx")] == 10          # two patterns of one class summed
    assert rows[("empty-ua", "2xx")] == 5
    assert rows[("other", "2xx")] == 100 - 30 - 10 - 5
    assert ("other", "4xx") not in rows              # all 4xx were googlebot: nothing left over
    assert all(r[1] == "halfonadouble.com" for r in out["cf_edge_daily"])

def test_agent_patterns_do_not_overlap():
    """A UA matching two patterns is double-counted and silently eats 'other'."""
    pats = [p.strip("%") for _, ps in cf_edge.AGENT_CLASSES for p in ps]
    for a in pats:
        for b in pats:
            assert a == b or a.lower() not in b.lower(), f"{a!r} is a substring of {b!r}"

def test_status_class_buckets():
    assert cf_edge.status_class(200) == "2xx"
    assert cf_edge.status_class("304") == "3xx"
    assert cf_edge.status_class(404) == "4xx"
    assert cf_edge.status_class(522) == "5xx"
    assert cf_edge.status_class(0) == "other"
    assert cf_edge.status_class(None) == "other"

def test_path_classes_generic_then_configured():
    prefixes = [("/stock/", "stock"), ("/learn/", "learn")]
    assert cf_edge.path_class("/robots.txt", prefixes) == "robots"
    assert cf_edge.path_class("/sitemap-0.xml", prefixes) == "sitemap"
    assert cf_edge.path_class("/sitemap-index.xml", prefixes) == "sitemap"
    assert cf_edge.path_class("/llms.txt", prefixes) == "llms"
    assert cf_edge.path_class("/stock/SPY.md", prefixes) == "markdown"
    assert cf_edge.path_class("/", prefixes) == "home"
    assert cf_edge.path_class("/_astro/about.css", prefixes) == "asset"
    assert cf_edge.path_class("/brand/hero.jpg", prefixes) == "asset"
    assert cf_edge.path_class("/stock/SPY", prefixes) == "stock"
    assert cf_edge.path_class("/learn/gamma-exposure", prefixes) == "learn"
    assert cf_edge.path_class("/admin/.env", prefixes) == "other"
    assert cf_edge.path_class("/stock/SPY", ()) == "other"

def test_googlebot_paths_count_requests_and_distinct_paths():
    groups = [
        {"count": 50, "dimensions": {"date": "2026-09-19", "clientRequestPath": "/robots.txt", "edgeResponseStatus": 200}},
        {"count": 4, "dimensions": {"date": "2026-09-19", "clientRequestPath": "/stock/SPY", "edgeResponseStatus": 200}},
        {"count": 3, "dimensions": {"date": "2026-09-19", "clientRequestPath": "/stock/GME", "edgeResponseStatus": 200}},
        {"count": 2, "dimensions": {"date": "2026-09-19", "clientRequestPath": "/stock/XYZ", "edgeResponseStatus": 404}},
    ]
    def fake(url, headers, method=None, body=None, timeout=None):
        if "limit: 10000" in body["query"]:
            return {"data": {"viewer": {"zones": [{"httpRequestsAdaptiveGroups": groups}]}}}
        return {"data": {"viewer": {"zones": [{}]}}}
    with mock.patch.object(cf_edge, "get_json", fake):
        out = cf_edge.collect("t", ZONES, path_classes={"halfonadouble.com": [("/stock/", "stock")]}, today=TODAY)
    rows = {(r[2], r[3]): (r[4], r[5]) for r in out["cf_googlebot_paths_daily"]}
    assert rows[("robots", "2xx")] == (50, 1)
    assert rows[("stock", "2xx")] == (7, 2)
    assert rows[("stock", "4xx")] == (2, 1)

def test_pages_functions_map_script_names_to_projects_and_paginate():
    rest = {
        "page=1": {"result": [{"name": "jedarden", "production_script_name": "pages-worker--1-production"}],
                   "result_info": {"total_pages": 2}},
        "page=2": {"result": [{"name": "devimprint", "production_script_name": "pages-worker--2-production"}],
                   "result_info": {"total_pages": 2}},
    }
    groups = [
        {"sum": {"requests": 10, "errors": 1, "subrequests": 3},
         "dimensions": {"date": "2026-09-19", "scriptName": "pages-worker--1-production", "status": "success"}},
        {"sum": {"requests": 2, "errors": 0, "subrequests": 0},
         "dimensions": {"date": "2026-09-19", "scriptName": "pages-worker--9-production", "status": "success"}},
    ]
    def fake(url, headers, method=None, body=None, timeout=None):
        if body is None:
            return rest[url.rsplit("?", 1)[1]]
        return {"data": {"viewer": {"accounts": [{"pagesFunctionsInvocationsAdaptiveGroups": groups}]}}}
    with mock.patch.object(cf_edge, "get_json", fake):
        out = cf_edge.collect_pages_functions("t", "acct", today=TODAY)
    assert out["cf_pages_functions_daily"] == [
        ("2026-09-19", "jedarden", "success", 10, 1, 3),
        ("2026-09-19", "pages-worker--9-production", "success", 2, 0, 0),  # unmapped keeps its id
    ]
