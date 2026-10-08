# estate-analytics — plan

## Goal
One daily process that snapshots estate analytics (GitHub traffic now; GSC and
CF RUM as credentials land) into `cnpg-ardenone` with row-level dedup, so
campaign effects (backlinks, HN, notes) are measurable over time despite
GitHub's 14-day retention.

## Architecture (decided)
- **Decision:** Deployment + internal daily loop. *Because:* org rule bans Job/CronJob.
  *Rejected:* CronJob (banned), ex44 cron (box crash history). *Enforced-by:* org-rule-guard hook.
- **Decision:** Postgres upserts on natural PKs as the dedup mechanism. *Because:* the
  sources re-serve overlapping windows daily. *Rejected:* append-only Parquet (dedup
  pushed to readers), git-committed JSON (was the stopgap in jedarden.com repo).
- **Decision:** CNPG `Database` + managed role + OpenBao→ESO secret, copied from the
  tradegraph blueprint. *Revisit-if:* the instance moves off cnpg-ardenone.
- **Decision:** optional sources skip on missing creds rather than fail. *Because:*
  GSC/CF human setup lands later; the GitHub leg must not wait.

## Phases
- [x] Phase 1: collector + schema + tests (this repo)
- [x] Phase 2: build wiring (iad-ci) + deploy wiring (declarative-config) + first live cycle (2026-08-25)
- [x] Phase 3: GSC + CF credentials land in OpenBao; sources activate (2026-09-07; GSC per property 2026-09-15)
- [x] Phase 4: dashboard.ardenone.com panel reading these tables (published as JSON datasets
      to the bucket after each cycle; the jedarden.com repo-traffic snapshots stay as the
      box-local readable record)
- [x] Phase 5 (2026-09-21, 0.4.0): crawl and indexation legs — zone-scoped Cloudflare edge
      requests by crawler class and Googlebot path class (client-facing only), Pages
      Functions invocations mapped to projects, and a weekly hash-stable URL Inspection
      sample per page class. Motivated by the pSEO launches on halfonadouble.com and
      devimprint.com: Cloudflare keeps the edge dataset ~32 days, and indexed share per
      page class is the number their launch gates are written in.
- [ ] Phase 6: panel sections for the Phase 5 datasets (dashboard-site repo)
- [ ] Phase 7 (0.5.0): GA4 leg — sessions by channel/source/country and per-page views
      and engagement, reusing the Search Console service account. It measures website
      sessions (which can include testing), the unit the
      halfonadouble.com ad-network gates are written in. Code shipped 2026-09-28; activates
      when the SA holds Viewer on the property, the Analytics Data API is enabled in
      `ardenone-analytics`, and `GA4_PROPERTIES` is set in declarative-config.
      Also publishes `ga4-gates`, `index-gate` and `striking-distance` (decision datasets;
      SQL verified against Postgres 16 with edge-case rows, 2026-10-03).
      Half on a Double activation (`estate-ce5b3d90`): property `557214664` and stream
      `G-YW2M6BSW0G` verified through the Admin and Data APIs on 2026-10-08 UTC.
      GitOps commit `a5632706` enables a 28-day lookback using the existing daily schedule.
      Direct reporting returned 77 sessions and 108 page views for October 3–6,
      including 4 Organic Search sessions; this is a short baseline, not evidence of growth.
      [Activation evidence](../analytics/2026-10-07-hoad-ga4-activation.json).
      First scheduled GA4 collection and dashboard publication remain to be verified.

## Open questions
None blocking. Grafana/dashboard surface is Phase 4 and undecided.
