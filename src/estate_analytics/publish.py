"""Publish dashboard datasets to the Garage bucket behind dashboard.ardenone.com.

The dashboard-site convention: `<prefix>/data/` is written directly to the
bucket by the dashboard's own aggregator pod, NOT by the dashboard-site repo's
CI, whose sync deliberately excludes `*/data/*` so it never deletes what a pod
just wrote. This module is that aggregator for the estate-analytics panel.

JSON rather than Parquet: sibling panels use both (catalyst-calendar and
options-downloader read .json, b2-usage and git-activity read .parquet), and at
these row counts JSON costs nothing and keeps the image free of pyarrow.

Publishing is skipped, not fatal, when S3 credentials are absent -- the same
discipline the optional collection sources follow.
"""
import datetime as dt, decimal, json

# Each entry becomes <prefix>/data/<name>.json.
#
# repo_referrers_daily holds a SNAPSHOT of GitHub's trailing-14-day referrer
# list as observed on snapshot_day -- not referrals that occurred that day.
# The panel must label it as such; summing it across days would multiply-count
# the same two weeks.
QUERIES = {
    "ai-referrals": """
        SELECT snapshot_day AS day, referrer, SUM(count) AS count,
               SUM(uniques) AS uniques
        FROM repo_referrers_daily
        WHERE referrer IN ('chatgpt.com','perplexity.ai','claude.ai','gemini.google.com',
                           'copilot.microsoft.com','Google','Bing','github.com','DuckDuckGo')
        GROUP BY snapshot_day, referrer
        ORDER BY snapshot_day, referrer""",
    "search": """
        SELECT day, SUM(clicks) AS clicks, SUM(impressions) AS impressions,
               ROUND(AVG(position)::numeric, 1) AS avg_position
        FROM gsc_daily GROUP BY day ORDER BY day""",
    "search-queries": """
        SELECT query, SUM(clicks) AS clicks, SUM(impressions) AS impressions,
               ROUND(AVG(position)::numeric, 1) AS avg_position
        FROM gsc_daily
        WHERE day >= CURRENT_DATE - INTERVAL '90 days'
        GROUP BY query ORDER BY SUM(impressions) DESC LIMIT 100""",
    "traffic": """
        SELECT day, host, SUM(pageviews) AS pageviews
        FROM cf_rum_daily GROUP BY day, host ORDER BY day, host""",
    "repo-traffic": """
        SELECT day, SUM(views) AS views, SUM(views_uniques) AS uniques,
               SUM(clones) AS clones
        FROM repo_traffic_daily GROUP BY day ORDER BY day""",
    "runs": """
        SELECT source, status, rows_upserted, finished_at
        FROM collect_runs ORDER BY id DESC LIMIT 60""",
}

def _jsonable(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    return v

def collect_datasets(conn):
    """Run every panel query. Returns {name: [row_dict, ...]}."""
    out = {}
    for name, sql in QUERIES.items():
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d.name for d in cur.description]
            out[name] = [{c: _jsonable(v) for c, v in zip(cols, row)}
                         for row in cur.fetchall()]
    return out

def build_meta(datasets):
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "rows": {k: len(v) for k, v in datasets.items()},
        "latest": {
            "search": max((r["day"] for r in datasets["search"]), default=None),
            "traffic": max((r["day"] for r in datasets["traffic"]), default=None),
            "repo_traffic": max((r["day"] for r in datasets["repo-traffic"]), default=None),
        },
    }

def publish(conn, s3, bucket, prefix):
    """Write every dataset plus meta.json. Returns the number of objects put."""
    datasets = collect_datasets(conn)
    payloads = {f"{k}.json": v for k, v in datasets.items()}
    payloads["meta.json"] = build_meta(datasets)
    for name, body in payloads.items():
        s3.put_object(
            Bucket=bucket,
            Key=f"{prefix.rstrip('/')}/data/{name}",
            Body=json.dumps(body, separators=(",", ":")).encode(),
            ContentType="application/json",
            CacheControl="max-age=300",
        )
    return len(payloads)
