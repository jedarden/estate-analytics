"""Idempotent DDL. Every table upserts on a natural primary key — that PK is the
deduplication: re-collecting an overlapping window (GitHub returns a trailing 14
days every day; GSC lags ~2 days) rewrites the same rows instead of duplicating."""

DDL = [
    """CREATE TABLE IF NOT EXISTS repo_traffic_daily (
        repo           text        NOT NULL,
        day            date        NOT NULL,
        views          integer     NOT NULL DEFAULT 0,
        views_uniques  integer     NOT NULL DEFAULT 0,
        clones         integer     NOT NULL DEFAULT 0,
        clones_uniques integer     NOT NULL DEFAULT 0,
        updated_at     timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (repo, day)
    )""",
    """CREATE TABLE IF NOT EXISTS repo_referrers_daily (
        snapshot_day date    NOT NULL,
        repo         text    NOT NULL,
        referrer     text    NOT NULL,
        count        integer NOT NULL,
        uniques      integer NOT NULL,
        PRIMARY KEY (snapshot_day, repo, referrer)
    )""",
    """CREATE TABLE IF NOT EXISTS repo_paths_daily (
        snapshot_day date    NOT NULL,
        repo         text    NOT NULL,
        path         text    NOT NULL,
        title        text,
        count        integer NOT NULL,
        uniques      integer NOT NULL,
        PRIMARY KEY (snapshot_day, repo, path)
    )""",
    """CREATE TABLE IF NOT EXISTS gsc_daily (
        day         date             NOT NULL,
        page        text             NOT NULL,
        query       text             NOT NULL,
        clicks      integer          NOT NULL,
        impressions integer          NOT NULL,
        position    double precision NOT NULL,
        PRIMARY KEY (day, page, query)
    )""",
    """CREATE TABLE IF NOT EXISTS cf_rum_daily (
        day          date    NOT NULL,
        path         text    NOT NULL,
        referer_host text    NOT NULL DEFAULT '',
        pageviews    integer NOT NULL,
        PRIMARY KEY (day, path, referer_host)
    )""",
    """CREATE TABLE IF NOT EXISTS collect_runs (
        id            bigserial   PRIMARY KEY,
        started_at    timestamptz NOT NULL DEFAULT now(),
        finished_at   timestamptz,
        source        text        NOT NULL,
        status        text        NOT NULL,
        rows_upserted integer     NOT NULL DEFAULT 0,
        message       text
    )""",
]

UPSERTS = {
    # Counts for the current (partial) day grow between collections; GREATEST keeps
    # the fullest observation. Fully elapsed days are stable, so this is exact.
    "repo_traffic_daily": """
        INSERT INTO repo_traffic_daily AS t
            (repo, day, views, views_uniques, clones, clones_uniques, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (repo, day) DO UPDATE SET
            views          = GREATEST(t.views,          EXCLUDED.views),
            views_uniques  = GREATEST(t.views_uniques,  EXCLUDED.views_uniques),
            clones         = GREATEST(t.clones,         EXCLUDED.clones),
            clones_uniques = GREATEST(t.clones_uniques, EXCLUDED.clones_uniques),
            updated_at     = now()""",
    "repo_referrers_daily": """
        INSERT INTO repo_referrers_daily (snapshot_day, repo, referrer, count, uniques)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (snapshot_day, repo, referrer) DO UPDATE SET
            count = EXCLUDED.count, uniques = EXCLUDED.uniques""",
    "repo_paths_daily": """
        INSERT INTO repo_paths_daily (snapshot_day, repo, path, title, count, uniques)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (snapshot_day, repo, path) DO UPDATE SET
            title = EXCLUDED.title, count = EXCLUDED.count, uniques = EXCLUDED.uniques""",
    "gsc_daily": """
        INSERT INTO gsc_daily (day, page, query, clicks, impressions, position)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (day, page, query) DO UPDATE SET
            clicks = EXCLUDED.clicks, impressions = EXCLUDED.impressions,
            position = EXCLUDED.position""",
    "cf_rum_daily": """
        INSERT INTO cf_rum_daily (day, path, referer_host, pageviews)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (day, path, referer_host) DO UPDATE SET
            pageviews = GREATEST(cf_rum_daily.pageviews, EXCLUDED.pageviews)""",
}
