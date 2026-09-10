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
| Google Search Console (armed; needs SA key) | trailing 7 days | `gsc_daily` | `(day, page, query)` |
| Cloudflare Web Analytics RUM (armed; needs token) | trailing 3 days | `cf_rum_daily` | `(day, host, path, referer_host)` |

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
| `GSC_SITE` | no (`sc-domain:jedarden.com`) | Search Console property |
| `CF_ANALYTICS_TOKEN` / `CF_ACCOUNT_ID` | optional | CF GraphQL RUM access |
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

## Deploy

Image `ronaldraygun/estate-analytics:<semver>` built by the `estate-analytics-build`
Argo WorkflowTemplate on iad-ci (VERSION auto-bump flow). Manifests live in
`jedarden/declarative-config` — `k8s/ardenone-cluster/estate-analytics/` plus the
CNPG `Database`/role/ExternalSecret in `k8s/ardenone-cluster/cnpg/`.

Local dev: `pip install -r requirements-dev.txt && PYTHONPATH=src pytest tests/`

## License

MIT — see [LICENSE](LICENSE).

---

Part of [jedarden.com](https://jedarden.com)

*This GitHub repo is a read-only mirror of git.ardenone.com/jedarden/estate-analytics — issues and PRs are welcome here either way.*
