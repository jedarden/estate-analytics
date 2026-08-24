"""Cloudflare Web Analytics (RUM) daily pageviews by path + referrer host via
GraphQL. Enabled only when CF_ANALYTICS_TOKEN is present."""
import datetime as dt
from ..http import get_json

def collect(token, account_id, days=3, today=None):
    today = today or dt.date.today()
    since = str(today - dt.timedelta(days=days))
    q = {"query": """
      query($acct: String!, $since: Date!) {
        viewer { accounts(filter: {accountTag: $acct}) {
          rumPageloadEventsAdaptiveGroups(
            limit: 10000,
            filter: {date_geq: $since},
            orderBy: [date_ASC]
          ) { count dimensions { date requestPath refererHost } }
        } }
      }""", "variables": {"acct": account_id, "since": since}}
    out = get_json("https://api.cloudflare.com/client/v4/graphql",
                   {"Authorization": f"Bearer {token}"}, method="POST", body=q)
    if out.get("errors"):
        raise RuntimeError(f"cf graphql: {out['errors'][:1]}")
    groups = out["data"]["viewer"]["accounts"][0]["rumPageloadEventsAdaptiveGroups"]
    rows = [(g["dimensions"]["date"], g["dimensions"]["requestPath"],
             g["dimensions"].get("refererHost") or "", g["count"]) for g in groups]
    return {"cf_rum_daily": rows}
