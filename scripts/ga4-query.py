#!/usr/bin/env python3
"""Query Google Analytics 4 from the CLI with the estate-analytics service account.

Credential: the Search Console key at ~/.config/gsc/service-account.json. The
service account needs Viewer on the GA4 property (GA Admin -> Property access
management), and its GCP project needs the Analytics Data API and Analytics
Admin API enabled.

Usage:
  ga4-query.py properties                       # properties this identity can read (Admin API)
  ga4-query.py report PROPERTY_ID [DAYS] [DIM...] [--exclude-automation]
                                                # default: 28 days by date
  ga4-query.py realtime PROPERTY_ID             # last 30 min by page -- verifies the tag fires

Metrics are sessions, screenPageViews, engagedSessions. Users are left out: they
are not additive across the rows this prints.

--exclude-automation applies the collector's automation classifier
(estate_analytics.sources.ga4.is_automated): cells of date x country x channel
with at least AUTOMATION_MIN_SESSIONS sessions and at most
AUTOMATION_MAX_ENGAGED_PCT% engaged are dropped, and a footer lists them.
"""
import collections, json, pathlib, sys, urllib.error
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from estate_analytics.http import get_json
from estate_analytics.sources import ga4, gsc

KEY = pathlib.Path.home() / ".config/gsc/service-account.json"

def token():
    return gsc._token(json.loads(KEY.read_text()), scope=ga4.SCOPE)

def call(url, tok, body=None):
    try:
        return get_json(url, {"Authorization": f"Bearer {tok}"},
                        method="POST" if body is not None else "GET", body=body)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} {url}\n{e.read().decode()[:600]}")

def properties(tok):
    out = call("https://analyticsadmin.googleapis.com/v1beta/accountSummaries", tok)
    if not out.get("accountSummaries"):
        print("no GA4 properties visible -- grant this service account Viewer on the property")
    for acct in out.get("accountSummaries", []):
        for p in acct.get("propertySummaries", []):
            print(f"{p['property'].split('/')[1]:>12}  {p['displayName']}  ({acct.get('displayName', '')})")

# The classifier's cell in GA4 Data API dimension names. The property is fixed
# per call. The collector sums sessionSource rows into the same cell.
CELL_DIMS = ["date", "country", "sessionDefaultChannelGroup"]

def flagged_cells(tok, pid, start, end):
    """{cell: (sessions, views, engaged)} for every flagged cell. Totals come
    from a query on the cell dimensions alone. Sessions by an event-scoped
    dimension such as pagePath do not add up to the cell's sessions."""
    rows = ga4.run_report(tok, pid, CELL_DIMS, ["sessions", "screenPageViews", "engagedSessions"],
                          start, end)
    return {tuple(r[:3]): (int(r[3]), int(r[4]), int(r[5])) for r in rows
            if ga4.is_automated(int(r[3]), int(r[5]))}

def report(tok, pid, days=28, dims=None, exclude_automation=False):
    import datetime as dt
    dims = dims or ["date"]
    metrics = ["sessions", "screenPageViews", "engagedSessions"]
    today = dt.date.today()
    start = today - dt.timedelta(days=days)
    if not exclude_automation:
        rows = ga4.run_report(tok, pid, dims, metrics, start, today)
    else:
        flagged = flagged_cells(tok, pid, start, today)
        query_dims = list(dict.fromkeys(dims + CELL_DIMS))
        cell_at = [query_dims.index(d) for d in CELL_DIMS]
        dim_at = [query_dims.index(d) for d in dims]
        kept = collections.defaultdict(lambda: [0, 0, 0])
        for r in ga4.run_report(tok, pid, query_dims, metrics, start, today):
            if tuple(r[i] for i in cell_at) in flagged:
                continue
            acc = kept[tuple(r[i] for i in dim_at)]
            for j, v in enumerate(r[len(query_dims):]):
                acc[j] += int(v)
        rows = [list(k) + [str(v) for v in m] for k, m in kept.items()]
    rows.sort()
    print("  ".join(dims), "|", "  ".join(metrics))
    for r in rows:
        print("  ".join(r[:len(dims)]), "|", "  ".join(f"{v:>6}" for v in r[len(dims):]))
    if exclude_automation:
        s, v, e = (sum(t[i] for t in flagged.values()) for i in range(3))
        print(f"# excluded {len(flagged)} automation cells ({' x '.join(CELL_DIMS)}): "
              f"{s} sessions, {v} views, {e} engaged. Rule: >= {ga4.AUTOMATION_MIN_SESSIONS} "
              f"sessions and <= {ga4.AUTOMATION_MAX_ENGAGED_PCT}% engaged")
        for cell, (cs, cv, ce) in sorted(flagged.items()):
            print(f"#   {'  '.join(cell)}  sessions={cs} engaged={ce}")

def realtime(tok, pid):
    body = {"dimensions": [{"name": "unifiedScreenName"}, {"name": "country"}],
            "metrics": [{"name": "screenPageViews"}, {"name": "activeUsers"}]}
    out = call(f"{ga4.API}/properties/{pid}:runRealtimeReport", tok, body)
    rows = out.get("rows", [])
    if not rows:
        print("no events in the last 30 minutes")
    for r in rows:
        d = [v["value"] for v in r["dimensionValues"]]
        m = [v["value"] for v in r["metricValues"]]
        print(f"{m[0]:>5} views {m[1]:>4} users  {d[1]:<16} {d[0]}")

def main(argv):
    exclude = "--exclude-automation" in argv
    argv = [a for a in argv if a != "--exclude-automation"]
    if not argv or argv[0] not in ("properties", "report", "realtime"):
        sys.exit(__doc__)
    tok = token()
    if argv[0] == "properties":
        properties(tok)
    elif argv[0] == "realtime":
        realtime(tok, argv[1])
    else:
        days = int(argv[2]) if len(argv) > 2 else 28
        report(tok, argv[1], days, argv[3:] or None, exclude_automation=exclude)

if __name__ == "__main__":
    main(sys.argv[1:])
