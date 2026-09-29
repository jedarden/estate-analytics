"""Google Analytics 4 daily traffic via the Data API (no SDK).

Enabled when GA4_PROPERTIES is set. Reuses the Search Console service account
(GSC_SA_JSON): GA4 grants are per property, so the same identity only needs
Viewer on each property plus the Analytics Data API enabled in its GCP project.

GA4 is the one source here that separates people from bots at the session
level and records engagement. Cloudflare RUM counts any JS-executing client
(bot spikes of 2,000+ "pageviews" a day on halfonadouble.com, 2026-09), and
Search Console only sees Google search. The ad-network gates the sites are
aiming at are stated in GA sessions and pageviews.

Two reports per property:
  ga4_daily        day x channel x source x country -- acquisition + audience
  ga4_pages_daily  day x host x path                -- per-page engagement

Users (totalUsers/activeUsers) are NOT additive across rows or days: a user
seen through two channels counts in both. Sum sessions and views; never sum
users. GA also revises the last ~2 days as processing completes, which the
overlapping window plus overwrite-upserts absorb.
"""
import datetime as dt, json
from ..http import get_json
from . import gsc

SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
API = "https://analyticsdata.googleapis.com/v1beta"
PAGE = 100000

DAILY_DIMS = ["date", "sessionDefaultChannelGroup", "sessionSource", "country"]
DAILY_METRICS = ["sessions", "engagedSessions", "totalUsers", "newUsers",
                 "screenPageViews", "userEngagementDuration"]
PAGE_DIMS = ["date", "hostName", "pagePath"]
PAGE_METRICS = ["screenPageViews", "activeUsers", "userEngagementDuration"]

def _day(v):
    """GA returns dates as YYYYMMDD."""
    return f"{v[0:4]}-{v[4:6]}-{v[6:8]}"

def run_report(tok, property_id, dims, metrics, start, end):
    """All rows of one report, paginated. Returns [(dim values..., metric values...)]."""
    rows, offset = [], 0
    while True:
        body = {"dateRanges": [{"startDate": str(start), "endDate": str(end)}],
                "dimensions": [{"name": d} for d in dims],
                "metrics": [{"name": m} for m in metrics],
                "limit": PAGE, "offset": offset, "keepEmptyRows": False}
        out = get_json(f"{API}/properties/{property_id}:runReport",
                       {"Authorization": f"Bearer {tok}"}, method="POST", body=body)
        batch = out.get("rows", [])
        for r in batch:
            rows.append([d["value"] for d in r["dimensionValues"]] +
                        [m["value"] for m in r["metricValues"]])
        offset += len(batch)
        if not batch or offset >= int(out.get("rowCount", 0)):
            return rows

def collect(sa_json, properties, days=7, today=None, tok=None):
    """properties: {label: numeric property id}, e.g. {'halfonadouble.com': '123456789'}.
    The label, not the id, is stored -- it is what every other table keys on."""
    tok = tok or gsc._token(json.loads(sa_json), scope=SCOPE)
    today = today or dt.date.today()
    start = today - dt.timedelta(days=days)
    daily, pages = [], []
    for label, pid in properties.items():
        for d, channel, source, country, s, es, tu, nu, pv, eng in run_report(
                tok, pid, DAILY_DIMS, DAILY_METRICS, start, today):
            daily.append((label, _day(d), channel, source, country,
                          int(s), int(es), int(tu), int(nu), int(pv), float(eng)))
        for d, host, path, pv, au, eng in run_report(
                tok, pid, PAGE_DIMS, PAGE_METRICS, start, today):
            pages.append((label, _day(d), host, path, int(pv), int(au), float(eng)))
    return {"ga4_daily": daily, "ga4_pages_daily": pages}
