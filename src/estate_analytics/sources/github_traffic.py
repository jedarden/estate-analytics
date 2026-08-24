"""GitHub traffic (views/clones per day, referrers, popular paths) for every
public non-fork repo of GITHUB_OWNER. Requires a token with push access
(traffic endpoints are owner-only). Returns rows keyed for the dedup upserts."""
import datetime as dt
from ..http import get_json

API = "https://api.github.com"

def _h(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

def list_repos(token, owner):
    repos, page = [], 1
    while True:
        batch = get_json(f"{API}/user/repos?affiliation=owner&per_page=100&page={page}", _h(token))
        if not batch:
            break
        repos += [r["name"] for r in batch
                  if r["owner"]["login"] == owner and not r["private"] and not r["fork"]]
        page += 1
    return sorted(repos)

def collect(token, owner, today=None):
    today = today or dt.date.today()
    traffic, referrers, paths = {}, [], []
    for repo in list_repos(token, owner):
        try:
            v = get_json(f"{API}/repos/{owner}/{repo}/traffic/views?per=day", _h(token))
            c = get_json(f"{API}/repos/{owner}/{repo}/traffic/clones?per=day", _h(token))
            for b in v.get("views", []):
                d = b["timestamp"][:10]
                traffic.setdefault((repo, d), [0, 0, 0, 0])[0:2] = [b["count"], b["uniques"]]
            for b in c.get("clones", []):
                d = b["timestamp"][:10]
                traffic.setdefault((repo, d), [0, 0, 0, 0])[2:4] = [b["count"], b["uniques"]]
            for r in get_json(f"{API}/repos/{owner}/{repo}/traffic/popular/referrers", _h(token)):
                referrers.append((today, repo, r["referrer"], r["count"], r["uniques"]))
            for p in get_json(f"{API}/repos/{owner}/{repo}/traffic/popular/paths", _h(token)):
                paths.append((today, repo, p["path"], p.get("title"), p["count"], p["uniques"]))
        except Exception as e:  # one repo failing must not sink the run
            print(f"github_traffic: {repo}: {e}", flush=True)
    traffic_rows = [(repo, day, *vals) for (repo, day), vals in sorted(traffic.items())]
    return {"repo_traffic_daily": traffic_rows,
            "repo_referrers_daily": referrers,
            "repo_paths_daily": paths}
