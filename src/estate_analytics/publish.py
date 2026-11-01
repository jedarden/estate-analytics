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
from .sources import ga4

# GA4 rows with the automation flag (see ga4.py). Every GA4 session or pageview
# query reads from this instead of ga4_daily, so raw data stays stored and the
# filter is applied only when a dataset is built. Column names are the same in
# every GA4 dataset:
#   engaged_sessions            GA's engaged sessions, all cells. Lead with this:
#                               it needs no classifier.
#   sessions_excl_automation    sessions outside flagged cells.
#   page_views_excl_automation  page views outside flagged cells.
#   automated_sessions          sessions in flagged cells.
#   raw_sessions, raw_page_views  unfiltered GA totals, for comparison only.
# The bare names `sessions` and `page_views` are deliberately not published, so
# an old reader fails instead of silently comparing bot traffic with a gate.
GA4_CLASSIFIED = f"({ga4.CLASSIFIED_DAILY_SQL}) g"
GA4_MEASURES = """
               SUM(engaged_sessions) AS engaged_sessions,
               COALESCE(SUM(sessions) FILTER (WHERE NOT automated), 0) AS sessions_excl_automation,
               COALESCE(SUM(page_views) FILTER (WHERE NOT automated), 0) AS page_views_excl_automation,
               COALESCE(SUM(sessions) FILTER (WHERE automated), 0) AS automated_sessions,
               SUM(sessions) AS raw_sessions,
               SUM(page_views) AS raw_page_views"""

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
        SELECT site, day, SUM(clicks) AS clicks, SUM(impressions) AS impressions,
               ROUND((SUM(position * impressions) / NULLIF(SUM(impressions), 0))::numeric, 1) AS avg_position
        FROM gsc_daily GROUP BY site, day ORDER BY site, day""",
    "search-queries": """
        SELECT site, query, SUM(clicks) AS clicks, SUM(impressions) AS impressions,
               ROUND((SUM(position * impressions) / NULLIF(SUM(impressions), 0))::numeric, 1) AS avg_position
        FROM gsc_daily
        WHERE day >= CURRENT_DATE - INTERVAL '90 days'
        GROUP BY site, query ORDER BY site, SUM(impressions) DESC LIMIT 500""",
    "search-pages": """
        SELECT site, page, SUM(clicks) AS clicks, SUM(impressions) AS impressions,
               ROUND((SUM(position * impressions) / NULLIF(SUM(impressions), 0))::numeric, 1) AS avg_position
        FROM gsc_daily
        WHERE day >= CURRENT_DATE - INTERVAL '90 days'
        GROUP BY site, page ORDER BY site, SUM(impressions) DESC LIMIT 500""",
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
    # Client-facing edge requests by crawler class; 'other' is everything not
    # matched by a named class and 'empty-ua' is the scanner population.
    "crawlers": """
        SELECT day, host, agent_class, status_class, requests
        FROM cf_edge_daily
        WHERE day >= CURRENT_DATE - INTERVAL '90 days'
        ORDER BY day, host, agent_class, status_class""",
    "googlebot-paths": """
        SELECT day, host, path_class, status_class, requests, distinct_paths
        FROM cf_googlebot_paths_daily
        WHERE day >= CURRENT_DATE - INTERVAL '90 days'
        ORDER BY day, host, path_class, status_class""",
    "pages-functions": """
        SELECT day, project, status, requests, errors, subrequests
        FROM cf_pages_functions_daily
        WHERE day >= CURRENT_DATE - INTERVAL '90 days'
        ORDER BY day, project, status""",
    # Indexed share per page class per weekly sample. verdict PASS is Google's
    # "URL is on Google"; coverage_state carries the reason otherwise.
    "index-sample": """
        SELECT sample_day, site, page_class, coverage_state,
               COUNT(*) AS urls,
               COUNT(*) FILTER (WHERE verdict = 'PASS') AS indexed
        FROM gsc_index_sample
        GROUP BY sample_day, site, page_class, coverage_state
        ORDER BY sample_day, site, page_class, coverage_state""",
    # The most recent sample's URLs, so a reader can see which pages Google
    # has and has not taken without a database connection.
    "index-sample-urls": """
        SELECT s.sample_day, s.site, s.page_class, s.url, s.verdict, s.coverage_state, s.last_crawl
        FROM gsc_index_sample s
        JOIN (SELECT site, MAX(sample_day) AS sample_day FROM gsc_index_sample GROUP BY site) m
          ON m.site = s.site AND m.sample_day = s.sample_day
        ORDER BY s.site, s.page_class, s.url""",
    # Progress against the ad-network gates in halfonadouble.com's growth audit
    # (docs/research/2026-09-12-pseo-growth-and-ad-monetization-audit.md s3.1):
    # Journey by Mediavine at 1,000 sessions/month, Raptive at 25,000
    # pageviews/month, and ad RPMs that depend on US/UK/CA/AU readers. Trailing
    # 30 days of GA4. The gates compare against sessions_excl_automation and
    # page_views_excl_automation, never the raw totals: in October 2026 about
    # 90% of raw sessions were flagged automation. Empty until GA4 is collecting.
    "ga4-gates": f"""
        SELECT property,{GA4_MEASURES},
               COALESCE(SUM(sessions) FILTER (WHERE NOT automated AND country IN
                   ('United States','United Kingdom','Canada','Australia')), 0)
                   AS tier1_sessions_excl_automation,
               1000  AS journey_sessions_gate,
               25000 AS raptive_pageviews_gate
        FROM {GA4_CLASSIFIED}
        WHERE day >= CURRENT_DATE - INTERVAL '30 days'
        GROUP BY property ORDER BY property""",
    # Indexed share per page class in each site's latest URL Inspection sample,
    # against the audit's Phase 1 gate (>= 80% of the first tier indexed).
    "index-gate": """
        SELECT s.site, s.page_class, s.sample_day,
               COUNT(*) AS urls,
               COUNT(*) FILTER (WHERE s.verdict = 'PASS') AS indexed,
               ROUND(100.0 * COUNT(*) FILTER (WHERE s.verdict = 'PASS') / COUNT(*), 1) AS indexed_pct,
               80 AS target_pct
        FROM gsc_index_sample s
        JOIN (SELECT site, MAX(sample_day) AS sample_day FROM gsc_index_sample GROUP BY site) m
          ON m.site = s.site AND m.sample_day = s.sample_day
        GROUP BY s.site, s.page_class, s.sample_day
        ORDER BY s.site, s.page_class""",
    # Striking distance: query/page pairs Google already shows on page 1-2
    # (average position 8-20) with real impressions but almost no clicks. The
    # cheapest search win there is: better title/description or one more
    # internal link, not a new page. Broad head terms at position 70+ are
    # excluded on purpose -- those need authority, not tweaks.
    "striking-distance": """
        SELECT site, page, query, SUM(clicks) AS clicks, SUM(impressions) AS impressions,
               ROUND((SUM(position * impressions) / SUM(impressions))::numeric, 1) AS avg_position
        FROM gsc_daily
        WHERE day >= CURRENT_DATE - INTERVAL '28 days'
        GROUP BY site, page, query
        HAVING SUM(impressions) >= 5
           AND SUM(position * impressions) / SUM(impressions) BETWEEN 8 AND 20
        ORDER BY SUM(impressions) DESC LIMIT 200""",
    # GA4 sessions by channel. Users are omitted on purpose: they are distinct
    # counts per row and summing them across channels or days overcounts.
    "ga4-traffic": f"""
        SELECT property, day, channel,{GA4_MEASURES},
               ROUND(SUM(engagement_sec)::numeric) AS engagement_sec
        FROM {GA4_CLASSIFIED}
        WHERE day >= CURRENT_DATE - INTERVAL '90 days'
        GROUP BY property, day, channel ORDER BY property, day, channel""",
    # Audience by country: ad RPMs are driven by US/UK/CA/AU traffic. Ranked by
    # engaged sessions, so a bot-heavy country does not top the list.
    "ga4-countries": f"""
        SELECT property, country,{GA4_MEASURES}
        FROM {GA4_CLASSIFIED}
        WHERE day >= CURRENT_DATE - INTERVAL '30 days'
        GROUP BY property, country
        ORDER BY property, SUM(engaged_sessions) DESC,
                 COALESCE(SUM(sessions) FILTER (WHERE NOT automated), 0) DESC, country""",
    # The cells the automation classifier flagged, so every exclusion can be
    # checked against GA itself. One row per (property, day, country, channel).
    "ga4-automation": f"""
        SELECT property, day, country, channel, SUM(sessions) AS sessions,
               SUM(engaged_sessions) AS engaged_sessions, SUM(page_views) AS page_views
        FROM {GA4_CLASSIFIED}
        WHERE automated AND day >= CURRENT_DATE - INTERVAL '90 days'
        GROUP BY property, day, country, channel
        ORDER BY property, day, country, channel""",
    # Per-page views are NOT filtered: ga4_pages_daily has no country or channel,
    # so the classifier cannot see it. Flagged sessions have one view each, so
    # they rarely reach this top 500, but read page_views here as raw.
    "ga4-pages": """
        SELECT property, host, path, SUM(page_views) AS page_views,
               ROUND((SUM(engagement_sec) / NULLIF(SUM(page_views), 0))::numeric, 1) AS engagement_sec_per_view
        FROM ga4_pages_daily
        WHERE day >= CURRENT_DATE - INTERVAL '90 days'
        GROUP BY property, host, path ORDER BY property, SUM(page_views) DESC LIMIT 500""",
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
            "crawlers": max((r["day"] for r in datasets.get("crawlers", [])), default=None),
            "index_sample": max((r["sample_day"] for r in datasets.get("index-sample", [])), default=None),
            "ga4": max((r["day"] for r in datasets.get("ga4-traffic", [])), default=None),
        },
        "ga4_automation_rule": ga4.AUTOMATION_RULE,
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
