# estate-analytics

Archives GitHub traffic beyond GitHub's rolling 14-day window, alongside
optional Google Search Console and Cloudflare Web Analytics data. Daily,
**deduplicated** snapshots are stored in Postgres so overlapping collection
windows update existing rows instead of double-counting them.

> **Author's deployment:** the checked-in defaults and operations notes target
> the jedarden estate and its `cnpg-ardenone` cluster. Reusers should supply
> their own owner, database, and source credentials.

A single long-lived collector (Deployment with an internal daily scheduler — no
k8s CronJobs in this estate) pulls three sources and upserts into tables keyed
by natural primary keys, so overlapping windows re-collect the same rows instead
of duplicating them:

| Source | Window pulled daily | Table(s) | Dedup key |
|---|---|---|---|
| GitHub traffic (all public non-fork repos) | trailing 14 days | `repo_traffic_daily`, `repo_referrers_daily`, `repo_paths_daily` | `(repo, day)` / `(snapshot_day, repo, referrer\|path)` |
| Google Search Console (armed; needs SA key) | trailing 7 days | `gsc_daily` | `(site, day, page, query)` |
| Cloudflare Web Analytics RUM (armed; needs token) | trailing 3 days | `cf_rum_daily` | `(day, host, path, referer_host)` |
| Cloudflare edge requests, zone-scoped, client-facing only (needs token with Zone Analytics: Read + `CF_ZONES`) | trailing 3 days; 32 on first run | `cf_edge_daily` (crawler classes), `cf_googlebot_paths_daily` (Googlebot by path class), `cf_pages_functions_daily` | `(day, host, agent_class, status_class)` / `(day, host, path_class, status_class)` / `(day, project, status)` |
| Google Analytics 4 (needs `GA4_PROPERTIES`; reuses the Search Console SA) | trailing 7 days, overwritten (GA revises recent days) | `ga4_daily` (channel/source/country), `ga4_pages_daily` | `(property, day, channel, source, country)` / `(property, day, host, path)` |
| Search Console URL Inspection sample (needs SA key + `GSC_INDEX_SAMPLE`) | weekly, fixed hash-stable sample per page class | `gsc_index_sample` | `(sample_day, site, url)` |

The edge source filters `requestSource: "eyeball"` unconditionally. Cloudflare's
dataset also carries its own internal records (Pages' Cache API lookups, whose
misses are logged as 504s by design) under the client's host and user agent;
without the filter they read as a large client-facing error rate that no client
received. Cloudflare retains the edge dataset for about a month, which is why it
is snapshotted at all.

Counts for a still-elapsing day only grow, so those upserts take `GREATEST`.
Every run is logged to `collect_runs`; startup runs a catch-up cycle if the last
success is older than `CATCHUP_AFTER_HOURS` (self-heal after downtime, safe
because GitHub retains 14 days).

## Configuration (env)

| Var | Required | Meaning |
|---|---|---|
| `DATABASE_URL` | yes | Postgres DSN (from the reflected `postgres-estate-analytics` secret) |
| `GITHUB_TOKEN` | for GitHub source | token with push access (traffic API is owner-only) |
| `GITHUB_OWNER` | no (jedarden) | account whose repos are collected |
| `GSC_SA_JSON` | optional | Google service-account key JSON (content, not a path) |
| `GSC_SITES` | no (`sc-domain:jedarden.com`) | comma-separated Search Console properties; `GSC_SITE` remains a legacy fallback |
| `CF_ANALYTICS_TOKEN` / `CF_ACCOUNT_ID` | optional | CF GraphQL RUM access |
| `CF_ZONES` | optional | `host=zone_id,...` — enables the edge source for those hosts (token needs Zone Analytics: Read and Cloudflare Pages: Read) |
| `CF_PATH_CLASSES` | no | `host=/prefix/:class,...;host2=...` — Googlebot path classes per host; robots, sitemap, llms, markdown, home and asset are classified before these |
| `CF_EDGE_DAYS` / `CF_EDGE_BACKFILL_DAYS` | no (3 / 32) | edge window; the backfill window applies while `cf_edge_daily` is empty |
| `GSC_INDEX_SAMPLE` | optional | `sc-domain:x=/prefix/:N,...;sc-domain:y=...` — URLs per page class to inspect; enables the weekly sample |
| `GSC_INDEX_EVERY_DAYS` | no (7) | sample cadence, paced off the last successful `gsc_index` run |
| `GA4_PROPERTIES` | optional | `label=propertyId,...` (e.g. `halfonadouble.com=123456789`); enables GA4. The `GSC_SA_JSON` identity needs Viewer on each property and the Analytics Data API enabled in its GCP project |
| `GA4_DAYS` | no (7) | GA4 window |
| `RUN_AT_UTC_HOUR` | no (5) | daily run hour, UTC |
| `DEST_S3_BUCKET` / `DEST_S3_ACCESS_KEY_ID` / `DEST_S3_SECRET_ACCESS_KEY` / `DEST_S3_ENDPOINT` | optional | Garage bucket for the dashboard panel; absent = publish skipped |
| `DEST_S3_PREFIX` | no (`estate-analytics`) | bucket prefix; datasets land at `<prefix>/data/*.json` |

Absent optional credentials skip that source with a log line — never a crash.

## Dashboard publish

After each cycle the collector writes the panel datasets for
`dashboard.ardenone.com/estate-analytics/` as JSON to the Garage `dashboard-site`
bucket under `estate-analytics/data/`. That path is deliberately **not** in the
dashboard-site repo: its CI syncs the repo over the bucket while excluding
`*/data/*`, so a pod's writes survive a site push. Publishing is skipped when S3
credentials are absent and a publish failure never fails the cycle — the
collection legs are the product, the panel is a view of them.

### Decision datasets

Three of the published datasets exist to be acted on, not just displayed:

| Dataset | Answers | Source tables |
|---|---|---|
| `ga4-gates` | Are we at the ad-network gates? 1,000 sessions/month (Journey), 25,000 pageviews/month (Raptive), and how many sessions come from US/UK/CA/AU. Trailing 30 days, compared using the figures that exclude automation (below). | `ga4_daily` |
| `index-gate` | What share of each page class is indexed in the latest URL Inspection sample, against the 80% Phase 1 target. | `gsc_index_sample` |
| `striking-distance` | Which query/page pairs already rank 8-20 with >= 5 impressions (last 28 days) -- the cheapest search wins. | `gsc_daily` |

The thresholds come from halfonadouble.com's growth audit
(`docs/research/2026-09-12-pseo-growth-and-ad-monetization-audit.md`, section 3.1)
and are literals in `publish.py`; change them in both places.

### GA4 automation filter

GA4 only filters known crawlers, so headless browsers that run the tag still
count as sessions. On halfonadouble.com from 2026-09-28 to 10-27, raw GA4
reported 10,279 sessions. Of these, 9,363 (91%) were in flagged cells, mostly
Singapore / Direct with 7 engaged sessions out of 9,325. Excluding automation
leaves 916 sessions and 332 engaged sessions. Raw data puts the site past the
1,000-session Journey gate, while the filtered figure keeps it short of it.

**Rule** (`sources/ga4.py`, `is_automated` and `CLASSIFIED_DAILY_SQL`): sum every
source in a *(property, day, country, channel)* cell. Flag the cell when it has
at least `AUTOMATION_MIN_SESSIONS = 10` sessions **and** at most
`AUTOMATION_MAX_ENGAGED_PCT = 5`% of them engaged. Near-zero engagement is the
bot signal. The floor protects small cells, because a few bounces from one
country in a day are noise. Real low-volume cells, such as Canada at about 4
sessions a day and 60% engaged, are never flagged. The rule names no country.

Flagging happens when data is read, never when it is stored: `ga4_daily` keeps
every raw row, and changing the constants reclassifies history on the next
publish. The GA4 datasets (`ga4-gates`, `ga4-traffic` and `ga4-countries`) all
publish the same columns:

| Column | Meaning |
|---|---|
| `engaged_sessions` | GA's engaged sessions. This is the lead figure because it needs no classifier. |
| `sessions_excl_automation`, `page_views_excl_automation` | Totals outside flagged cells. The gates use these. |
| `automated_sessions` | Sessions in flagged cells. |
| `raw_sessions`, `raw_page_views` | Unfiltered GA totals, kept for comparison. |

Bare `sessions` and `page_views` columns are not published, so a reader built on
the earlier schema fails instead of comparing bot traffic with a gate.
`ga4-countries` is ranked by engaged sessions. `ga4-automation` lists every
flagged cell from the last 90 days, so each exclusion can be checked in GA.
`meta.json` carries the rule in force as `ga4_automation_rule`. `ga4-pages`
remains unfiltered: `ga4_pages_daily` has no country or channel. To see the
same filter directly in GA, run
`scripts/ga4-query.py report PROPERTY_ID [DAYS] [DIM...] --exclude-automation`.

## Deploy

Image `ronaldraygun/estate-analytics:<semver>` built by the `estate-analytics-build`
Argo WorkflowTemplate on iad-ci (VERSION auto-bump flow). Manifests live in
`jedarden/declarative-config` — `k8s/ardenone-cluster/estate-analytics/` plus the
CNPG `Database`/role/ExternalSecret in `k8s/ardenone-cluster/cnpg/`.

From codinghome, `scripts/ga4-query.py` (`properties`, `report`, `realtime`) queries
GA4 directly with the same service-account key as `gsc-query.py`; `report
--exclude-automation` applies the automation filter above.

Local dev: `pip install -r requirements-dev.txt && PYTHONPATH=src pytest tests/`

## License

MIT — see [LICENSE](LICENSE).

---

Part of [jedarden.com](https://jedarden.com)

*This GitHub repo is a read-only mirror of git.ardenone.com/jedarden/estate-analytics — issues and PRs are welcome here either way.*
