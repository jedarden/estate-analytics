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
- [ ] Phase 2: build wiring (iad-ci) + deploy wiring (declarative-config) + first live cycle
- [ ] Phase 3: GSC + CF credentials land in OpenBao; sources activate
- [ ] Phase 4 (later): dashboard.ardenone.com panel reading these tables; retire the
      jedarden.com repo-traffic JSON snapshots

## Open questions
None blocking. Grafana/dashboard surface is Phase 4 and undecided.
