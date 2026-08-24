"""Google Search Console search analytics via a service-account JWT (no SDK).
Enabled only when GSC_SA_JSON (the key's JSON content) is present."""
import datetime as dt, json, time, urllib.parse
import jwt
from ..http import get_json

SCOPE = "https://www.googleapis.com/auth/webmasters"
API = "https://www.googleapis.com/webmasters/v3"

def _token(sa):
    now = int(time.time())
    assertion = jwt.encode(
        {"iss": sa["client_email"], "scope": SCOPE, "aud": sa["token_uri"],
         "iat": now, "exp": now + 3600},
        sa["private_key"], algorithm="RS256")
    import urllib.request
    data = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion}).encode()
    with urllib.request.urlopen(urllib.request.Request(sa["token_uri"], data=data)) as f:
        return json.load(f)["access_token"]

def collect(sa_json, site, days=7, today=None):
    sa = json.loads(sa_json)
    tok = _token(sa)
    today = today or dt.date.today()
    body = {"startDate": str(today - dt.timedelta(days=days)),
            "endDate": str(today),
            "dimensions": ["date", "page", "query"],
            "rowLimit": 25000}
    out = get_json(f"{API}/sites/{urllib.parse.quote(site, safe='')}/searchAnalytics/query",
                   {"Authorization": f"Bearer {tok}"}, method="POST", body=body)
    rows = [(r["keys"][0], r["keys"][1], r["keys"][2],
             int(r["clicks"]), int(r["impressions"]), float(r["position"]))
            for r in out.get("rows", [])]
    return {"gsc_daily": rows}
