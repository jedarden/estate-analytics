"""Cloudflare edge request analytics -- zone-scoped and client-facing only.

Three tables from two GraphQL datasets:

  cf_edge_daily             requests per (day, host, agent_class, status_class)
  cf_googlebot_paths_daily  Googlebot requests per (day, host, path_class, status_class)
  cf_pages_functions_daily  Pages Functions invocations per (day, project, status)

The RUM dataset only sees browsers that ran the beacon; this one sees every
request, which is the only place crawler activity (Googlebot, GPTBot,
ClaudeBot, ...) is visible. Cloudflare keeps it for ~32 days on this plan, so
without a daily snapshot the indexation curve of a new site cannot be
reconstructed after the fact.

requestSource:"eyeball" is NOT optional. The dataset also holds Cloudflare's
own internal records -- the Pages asset Worker's Cache API lookups (a miss is
logged as a 504 with requestSource edgeWorkerCacheAPI, by design) and
early-hints cache fetches -- and those rows inherit the client's host and user
agent. Unfiltered, they read as a ~25% client-facing 504 rate that no client
ever received (2026-09-18, reported twice before it was checked).

Agent classes are resolved server-side with one aliased sub-query per pattern.
"other" is derived as all minus the named classes minus empty-UA, so the
patterns must not overlap: a user agent matching two patterns would be counted
twice and push "other" negative (it is floored at zero, which would then hide
the mistake). Spoofed crawlers cannot be told apart here -- clientIPClass is
not available on this plan -- which is why status_class travels alongside:
scanners wearing Googlebot's UA fetch .env files and get 404s.
"""
import datetime as dt
from ..http import get_json

GQL = "https://api.cloudflare.com/client/v4/graphql"
REST = "https://api.cloudflare.com/client/v4"

# (class, [userAgent_like patterns]). One alias is issued per pattern.
AGENT_CLASSES = [
    ("googlebot",        ["%Googlebot%"]),
    ("bingbot",          ["%bingbot%"]),
    ("gptbot",           ["%GPTBot%"]),
    ("oai-searchbot",    ["%OAI-SearchBot%"]),
    ("chatgpt-user",     ["%ChatGPT-User%"]),
    ("claudebot",        ["%ClaudeBot%"]),
    ("claude-searchbot", ["%Claude-SearchBot%"]),
    ("claude-user",      ["%Claude-User%"]),
    ("perplexitybot",    ["%PerplexityBot%"]),
    ("perplexity-user",  ["%Perplexity-User%"]),
    ("google-extended",  ["%Google-Extended%"]),
    ("amazonbot",        ["%Amazonbot%"]),
    ("applebot",         ["%Applebot%"]),
    ("bytespider",       ["%Bytespider%"]),
    ("meta-external",    ["%meta-external%"]),
    ("duckassistbot",    ["%DuckAssistBot%"]),
    ("ccbot",            ["%CCBot%"]),
    ("seo-tools",        ["%AhrefsBot%", "%SemrushBot%", "%MJ12bot%"]),
]

STATIC_EXT = (".css", ".js", ".mjs", ".map", ".png", ".jpg", ".jpeg", ".webp",
              ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".avif", ".webmanifest")

def status_class(status):
    try:
        s = int(status)
    except (TypeError, ValueError):
        return "other"
    return f"{s // 100}xx" if 100 <= s < 600 else "other"

def path_class(path, prefixes=()):
    """Generic classes first, then the host's configured prefixes (first match)."""
    p = path or "/"
    if p == "/robots.txt":
        return "robots"
    if "sitemap" in p and p.endswith(".xml"):
        return "sitemap"
    if p in ("/llms.txt", "/llms-full.txt"):
        return "llms"
    if p.endswith(".md"):
        return "markdown"
    if p == "/":
        return "home"
    if p.startswith(("/_astro/", "/pagefind/", "/_next/")) or p.lower().endswith(STATIC_EXT):
        return "asset"
    for prefix, cls in prefixes:
        if p.startswith(prefix):
            return cls
    return "other"

def _since(today, days):
    return f"{today - dt.timedelta(days=days)}T00:00:00Z"

def _post(token, query, variables):
    out = get_json(GQL, {"Authorization": f"Bearer {token}"}, method="POST",
                   body={"query": query, "variables": variables}, timeout=120)
    if out.get("errors"):
        raise RuntimeError(f"cf graphql: {out['errors'][:1]}")
    return out["data"]["viewer"]

_SUB = ('httpRequestsAdaptiveGroups(limit: 2000, filter: {datetime_geq: $since, '
        'requestSource: "eyeball", clientRequestHTTPHost: $host%s}, orderBy: [date_ASC]) '
        '{ count dimensions { date edgeResponseStatus } }')

def _agent_aliases():
    """[(alias, class, extra filter fragment)] -- the 'all' and 'empty' aliases
    frame the derivation of 'other'."""
    aliases = [("all", None, ""), ("empty", "empty-ua", ', userAgent: ""')]
    for cls, patterns in AGENT_CLASSES:
        for i, pat in enumerate(patterns):
            aliases.append((f"{cls.replace('-', '_')}_{i}", cls, f', userAgent_like: "{pat}"'))
    return aliases

def _batched(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]

def _crawler_rows(token, host, zone, since, batch_size):
    cells = {}  # (day, status_class) -> {class: count}
    for batch in _batched(_agent_aliases(), batch_size):
        body = " ".join(f"{alias}: {_SUB % extra}" for alias, _, extra in batch)
        q = ("query($zone: String!, $since: Time!, $host: String!) "
             "{ viewer { zones(filter: {zoneTag: $zone}) { %s } } }" % body)
        zones = _post(token, q, {"zone": zone, "since": since, "host": host})["zones"]
        data = zones[0] if zones else {}
        for alias, cls, _ in batch:
            key = "all" if cls is None else cls
            for g in data.get(alias) or []:
                cell = cells.setdefault((g["dimensions"]["date"],
                                         status_class(g["dimensions"]["edgeResponseStatus"])), {})
                cell[key] = cell.get(key, 0) + int(g["count"])
    rows = []
    for (day, sc), counts in sorted(cells.items()):
        named = {k: v for k, v in counts.items() if k != "all"}
        other = counts.get("all", 0) - sum(named.values())
        for cls, n in sorted(named.items()):
            if n:
                rows.append((day, host, cls, sc, n))
        if other > 0:
            rows.append((day, host, "other", sc, other))
    return rows

_PATHS_Q = ("query($zone: String!, $since: Time!, $host: String!) { viewer { zones(filter: {zoneTag: $zone}) { "
            'httpRequestsAdaptiveGroups(limit: 10000, filter: {datetime_geq: $since, requestSource: "eyeball", '
            'clientRequestHTTPHost: $host, userAgent_like: "%Googlebot%"}, orderBy: [count_DESC]) '
            "{ count dimensions { date clientRequestPath edgeResponseStatus } } } } }")

def _googlebot_rows(token, host, zone, since, prefixes):
    zones = _post(token, _PATHS_Q, {"zone": zone, "since": since, "host": host})["zones"]
    groups = (zones[0] if zones else {}).get("httpRequestsAdaptiveGroups") or []
    cells = {}  # (day, path_class, status_class) -> [requests, {paths}]
    for g in groups:
        d = g["dimensions"]
        key = (d["date"], path_class(d["clientRequestPath"], prefixes), status_class(d["edgeResponseStatus"]))
        cell = cells.setdefault(key, [0, set()])
        cell[0] += int(g["count"])
        cell[1].add(d["clientRequestPath"])
    return [(day, host, pc, sc, n, len(paths))
            for (day, pc, sc), (n, paths) in sorted(cells.items())]

def collect(token, zones, path_classes=None, days=3, today=None, batch_size=8):
    """zones: {host: zone_id}. path_classes: {host: [(prefix, class), ...]}."""
    today = today or dt.date.today()
    since = _since(today, days)
    edge, paths = [], []
    for host, zone in zones.items():
        edge += _crawler_rows(token, host, zone, since, batch_size)
        paths += _googlebot_rows(token, host, zone, since, (path_classes or {}).get(host, ()))
    return {"cf_edge_daily": edge, "cf_googlebot_paths_daily": paths}

_FUNCS_Q = ("query($acct: String!, $since: Time!) { viewer { accounts(filter: {accountTag: $acct}) { "
            "pagesFunctionsInvocationsAdaptiveGroups(limit: 5000, filter: {datetime_geq: $since}, orderBy: [date_ASC]) "
            "{ sum { requests errors subrequests } dimensions { date scriptName status } } } } }")

def pages_projects(token, account_id):
    """{production_script_name: project name}. The list endpoint rejects
    per_page, so walk its pages via result_info."""
    out, page = {}, 1
    while True:
        r = get_json(f"{REST}/accounts/{account_id}/pages/projects?page={page}",
                     {"Authorization": f"Bearer {token}"})
        for p in r.get("result") or []:
            if p.get("production_script_name"):
                out[p["production_script_name"]] = p["name"]
        info = r.get("result_info") or {}
        if page >= int(info.get("total_pages") or 1) or not r.get("result"):
            return out
        page += 1

def collect_pages_functions(token, account_id, days=3, today=None):
    today = today or dt.date.today()
    names = pages_projects(token, account_id)
    accounts = _post(token, _FUNCS_Q, {"acct": account_id, "since": _since(today, days)})["accounts"]
    groups = (accounts[0] if accounts else {}).get("pagesFunctionsInvocationsAdaptiveGroups") or []
    rows = {}
    for g in groups:
        d, s = g["dimensions"], g["sum"]
        key = (d["date"], names.get(d["scriptName"], d["scriptName"]), d.get("status") or "")
        cur = rows.setdefault(key, [0, 0, 0])
        cur[0] += int(s["requests"]); cur[1] += int(s["errors"]); cur[2] += int(s.get("subrequests") or 0)
    return {"cf_pages_functions_daily": [(*k, *v) for k, v in sorted(rows.items())]}
