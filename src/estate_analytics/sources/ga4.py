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

GA4's built-in bot filter only drops known crawlers, so headless browsers that
run the tag still arrive as sessions. The automation classifier at the bottom
of this module flags them at read time; ga4_daily always keeps the raw rows.
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


# --- Automation classifier ----------------------------------------------------
# Evidence: on halfonadouble.com from 2026-09-28 to 10-27, Singapore / Direct
# carried 9,302 of 10,203 sessions with 7 engaged. Those sessions were desktop
# Chrome, with one pageview each, on about 5,600 distinct landing pages, in
# bursts on 10-13 to 10-16 and 10-23. In the same window, every other cell
# (property, day, country, channel) with 10 or more sessions had at least 20%
# engaged, except Brazil and Colombia / Direct on 10-19 (24 and 14 sessions,
# none engaged), which this rule also flags. Real low-volume traffic looks
# like Canada / Organic Search: about 4 sessions a day, about 60% engaged.
#
# Rule: group by AUTOMATION_CELL, summing every source in the cell. Flag the
# cell as automation when
#     sessions >= AUTOMATION_MIN_SESSIONS
#     and engaged_sessions <= AUTOMATION_MAX_ENGAGED_PCT% of sessions.
# Both conditions are required. Engagement near zero is the bot signal. The
# session floor stops a few one-page human visits from a small country being
# flagged: below 10 sessions, zero engaged is ordinary noise. Integer
# arithmetic keeps the boundary identical in Python and in SQL.
#
# The rule is applied when data is read: CLASSIFIED_DAILY_SQL for the
# published datasets and `ga4-query.py report --exclude-automation` for direct
# queries. Raw rows are never dropped or rewritten, so the constants can be
# changed and every past day is reclassified on the next publish.
AUTOMATION_CELL = ("property", "day", "country", "channel")
AUTOMATION_MIN_SESSIONS = 10
AUTOMATION_MAX_ENGAGED_PCT = 5

def is_automated(sessions, engaged_sessions):
    """True when one cell's totals match the automation rule above."""
    return (sessions >= AUTOMATION_MIN_SESSIONS
            and 100 * engaged_sessions <= AUTOMATION_MAX_ENGAGED_PCT * sessions)

def automated_cells(daily_rows):
    """Return the flagged (property, day, country, channel) keys.

    daily_rows are ga4_daily tuples in the order collect() returns them. A cell
    is classified on its totals across all sources, matching the SQL window.
    """
    totals = {}
    for prop, day, channel, _source, country, sessions, engaged, *_ in daily_rows:
        t = totals.setdefault((prop, day, country, channel), [0, 0])
        t[0] += sessions
        t[1] += engaged
    return {cell for cell, (s, e) in totals.items() if is_automated(s, e)}

# The ga4_daily rows plus an `automated` boolean. Publish queries select FROM
# (CLASSIFIED_DAILY_SQL) instead of FROM ga4_daily. The window partition is the
# cell, so a day filter in the outer query never splits a cell.
CLASSIFIED_DAILY_SQL = f"""
        SELECT property, day, channel, source, country, sessions, engaged_sessions,
               total_users, new_users, page_views, engagement_sec,
               (SUM(sessions) OVER cell >= {AUTOMATION_MIN_SESSIONS}
                AND 100 * SUM(engaged_sessions) OVER cell
                    <= {AUTOMATION_MAX_ENGAGED_PCT} * SUM(sessions) OVER cell) AS automated
        FROM ga4_daily
        WINDOW cell AS (PARTITION BY {", ".join(AUTOMATION_CELL)})"""

# Published in meta.json so a reader of the datasets can see the rule in force.
AUTOMATION_RULE = {
    "cell": list(AUTOMATION_CELL),
    "min_sessions": AUTOMATION_MIN_SESSIONS,
    "max_engaged_pct": AUTOMATION_MAX_ENGAGED_PCT,
}
