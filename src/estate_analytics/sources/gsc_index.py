"""Weekly indexation sample via the Search Console URL Inspection API.

Search Console's sitemap report no longer returns an indexed count, and the
searchAnalytics endpoint only sees pages that already earn impressions. The
only per-URL indexation signal is URL Inspection (quota 2000/day per property),
so a fixed sample of each page class is inspected once a week and stored with
its coverage state. Indexed share per class over time is the metric a
programmatic-SEO launch is judged by; the gate for halfonadouble's Phase 1 is
"80% of Tier A indexed within six weeks".

The sample is stable by construction: URLs are ordered by the SHA-1 of the URL
and the first N per class are taken. Reordering the sitemap changes nothing,
and a new page displaces a pick only when it hashes ahead of one, so a few
additions to a large class leave the sample almost entirely intact. A
random.sample would re-roll every week and turn the trend into noise.
"""
import datetime as dt, hashlib, json, re, urllib.parse
from ..http import get_json, get_text
from .gsc import _token

INSPECT = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"

def site_host(site):
    return site.split(":", 1)[1] if site.startswith("sc-domain:") else urllib.parse.urlparse(site).netloc

def _locs(xml):
    return [m.strip() for m in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml or "")]

def sitemap_urls(host, fetch=get_text):
    """Every <loc> reachable from /sitemap-index.xml (or /sitemap.xml)."""
    for start in (f"https://{host}/sitemap-index.xml", f"https://{host}/sitemap.xml"):
        try:
            top = fetch(start)
        except Exception:
            continue
        locs = _locs(top)
        if not locs:
            continue
        if "<sitemapindex" in top:
            urls = []
            for child in locs:
                urls += _locs(fetch(child))
            return urls
        return locs
    return []

def pick(urls, host, prefix, n):
    """Stable first-N of the class by URL hash."""
    pool = [u for u in urls if u.replace(f"https://{host}", "", 1).startswith(prefix)]
    ranked = sorted(pool, key=lambda u: hashlib.sha1(u.encode()).hexdigest())
    return ranked[:n]

def inspect(tok, site, url):
    r = get_json(INSPECT, {"Authorization": f"Bearer {tok}"}, method="POST",
                 body={"inspectionUrl": url, "siteUrl": site}, timeout=60)
    return r.get("inspectionResult", {}).get("indexStatusResult", {})

def _ts(s):
    if not s:
        return None
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))

def collect(sa_json, spec, today=None, fetch=get_text, inspect_fn=None):
    """spec: {site: [(prefix, n), ...]}. Returns gsc_index_sample rows."""
    today = today or dt.date.today()
    tok = _token(json.loads(sa_json))
    do_inspect = inspect_fn or (lambda site, url: inspect(tok, site, url))
    rows = []
    for site, classes in spec.items():
        host = site_host(site)
        urls = sitemap_urls(host, fetch)
        if not urls:
            print(f"gsc_index: {site}: no sitemap URLs, skipped", flush=True)
            continue
        for prefix, n in classes:
            for url in pick(urls, host, prefix, int(n)):
                try:
                    r = do_inspect(site, url)
                except Exception as e:  # one URL must not sink the sample
                    print(f"gsc_index: {url}: {e}", flush=True)
                    continue
                rows.append((today, site, prefix, url,
                             r.get("verdict") or "", r.get("coverageState") or "",
                             r.get("indexingState") or "", r.get("pageFetchState") or "",
                             r.get("robotsTxtState") or "", _ts(r.get("lastCrawlTime")),
                             r.get("googleCanonical") or ""))
    return {"gsc_index_sample": rows}
