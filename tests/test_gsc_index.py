"""Index sample: stable URL pick, sitemap-index walking, row shape."""
import datetime as dt
from unittest import mock
from estate_analytics.sources import gsc_index

TODAY = dt.date(2026, 9, 20)
HOST = "halfonadouble.com"
URLS = [f"https://{HOST}/stock/{t}" for t in ("SPY", "GME", "QQQ", "NVDA", "TSLA", "AMD", "MU")] \
     + [f"https://{HOST}/learn/gamma-exposure", f"https://{HOST}/screens/nearest-max-pain"]

def test_pick_is_stable_and_order_independent():
    a = gsc_index.pick(URLS, HOST, "/stock/", 3)
    b = gsc_index.pick(list(reversed(URLS)), HOST, "/stock/", 3)
    assert a == b and len(a) == 3
    assert all("/stock/" in u for u in a)

def test_pick_survives_sitemap_growth():
    """Adding pages must not re-roll the sample. Under hash ordering a new URL
    displaces a pick only if it hashes ahead of one, so a few additions to a
    large class leave the sample almost entirely intact (random.sample would
    replace all of it)."""
    pool = [f"https://{HOST}/stock/T{i}" for i in range(200)]
    before = gsc_index.pick(pool, HOST, "/stock/", 10)
    after = gsc_index.pick(pool + [f"https://{HOST}/stock/NEW{i}" for i in range(5)], HOST, "/stock/", 10)
    assert len(set(before) & set(after)) >= 9

def test_pick_respects_class_prefix_and_size():
    assert gsc_index.pick(URLS, HOST, "/learn/", 5) == [f"https://{HOST}/learn/gamma-exposure"]
    assert gsc_index.pick(URLS, HOST, "/missing/", 5) == []

def test_sitemap_index_is_walked():
    pages = {
        f"https://{HOST}/sitemap-index.xml":
            f'<sitemapindex><sitemap><loc>https://{HOST}/sitemap-0.xml</loc></sitemap></sitemapindex>',
        f"https://{HOST}/sitemap-0.xml":
            f'<urlset><url><loc> https://{HOST}/stock/SPY </loc></url><url><loc>https://{HOST}/</loc></url></urlset>',
    }
    urls = gsc_index.sitemap_urls(HOST, fetch=lambda u: pages[u])
    assert urls == [f"https://{HOST}/stock/SPY", f"https://{HOST}/"]

def test_plain_sitemap_fallback():
    def fetch(u):
        if u.endswith("sitemap-index.xml"):
            raise OSError("404")
        return f"<urlset><url><loc>https://{HOST}/a</loc></url></urlset>"
    assert gsc_index.sitemap_urls(HOST, fetch=fetch) == [f"https://{HOST}/a"]

def test_collect_rows_carry_class_and_states():
    pages = {f"https://{HOST}/sitemap-index.xml":
             "<urlset>" + "".join(f"<url><loc>{u}</loc></url>" for u in URLS) + "</urlset>"}
    result = {"verdict": "NEUTRAL", "coverageState": "URL is unknown to Google",
              "indexingState": "INDEXING_ALLOWED", "pageFetchState": "SUCCESSFUL",
              "robotsTxtState": "ALLOWED", "lastCrawlTime": "2026-09-15T10:00:00Z"}
    seen = []
    def inspect(site, url):
        seen.append((site, url)); return result
    with mock.patch.object(gsc_index, "_token", lambda sa: "tok"):
        out = gsc_index.collect('{"client_email":"x"}', {"sc-domain:halfonadouble.com": [("/stock/", 2), ("/learn/", 1)]},
                                today=TODAY, fetch=lambda u: pages[u], inspect_fn=inspect)
    rows = out["gsc_index_sample"]
    assert len(rows) == 3 and len(seen) == 3
    assert {r[2] for r in rows} == {"/stock/", "/learn/"}
    r = rows[0]
    assert r[0] == TODAY and r[1] == "sc-domain:halfonadouble.com"
    assert r[4] == "NEUTRAL" and r[5] == "URL is unknown to Google"
    assert r[9] == dt.datetime(2026, 9, 15, 10, tzinfo=dt.timezone.utc)

def test_one_failed_inspection_does_not_sink_the_sample():
    pages = {f"https://{HOST}/sitemap-index.xml":
             "<urlset>" + "".join(f"<url><loc>{u}</loc></url>" for u in URLS) + "</urlset>"}
    def inspect(site, url):
        if url.endswith(gsc_index.pick(URLS, HOST, "/stock/", 2)[0].rsplit("/", 1)[1]):
            raise RuntimeError("429")
        return {"verdict": "PASS"}
    with mock.patch.object(gsc_index, "_token", lambda sa: "tok"):
        out = gsc_index.collect('{}', {"sc-domain:halfonadouble.com": [("/stock/", 2)]},
                                today=TODAY, fetch=lambda u: pages[u], inspect_fn=inspect)
    assert len(out["gsc_index_sample"]) == 1

def test_site_host():
    assert gsc_index.site_host("sc-domain:devimprint.com") == "devimprint.com"
    assert gsc_index.site_host("https://jedarden.com/") == "jedarden.com"
