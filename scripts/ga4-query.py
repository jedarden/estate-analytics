#!/usr/bin/env python3
"""Query Google Analytics 4 from the CLI with the estate-analytics service account.

Credential: the Search Console key at ~/.config/gsc/service-account.json. The
service account needs Viewer on the GA4 property (GA Admin -> Property access
management), and its GCP project needs the Analytics Data API and Analytics
Admin API enabled.

Usage:
  ga4-query.py properties                       # properties this identity can read (Admin API)
  ga4-query.py report PROPERTY_ID [DAYS] [DIM...]   # default: 28 days by date
  ga4-query.py realtime PROPERTY_ID             # last 30 min by page -- verifies the tag fires

Metrics are sessions, screenPageViews, engagedSessions. Users are left out: they
are not additive across the rows this prints.
"""
import json, pathlib, sys, urllib.error
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

def report(tok, pid, days=28, dims=None):
    import datetime as dt
    dims = dims or ["date"]
    metrics = ["sessions", "screenPageViews", "engagedSessions"]
    today = dt.date.today()
    rows = ga4.run_report(tok, pid, dims, metrics, today - dt.timedelta(days=days), today)
    rows.sort()
    print("  ".join(dims), "|", "  ".join(metrics))
    for r in rows:
        print("  ".join(r[:len(dims)]), "|", "  ".join(f"{v:>6}" for v in r[len(dims):]))

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
    if not argv or argv[0] not in ("properties", "report", "realtime"):
        sys.exit(__doc__)
    tok = token()
    if argv[0] == "properties":
        properties(tok)
    elif argv[0] == "realtime":
        realtime(tok, argv[1])
    else:
        days = int(argv[2]) if len(argv) > 2 else 28
        report(tok, argv[1], days, argv[3:] or None)

if __name__ == "__main__":
    main(sys.argv[1:])
