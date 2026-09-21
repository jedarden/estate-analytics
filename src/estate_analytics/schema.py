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
        site        text             NOT NULL,
        day         date             NOT NULL,
        page        text             NOT NULL,
        query       text             NOT NULL,
        clicks      integer          NOT NULL,
        impressions integer          NOT NULL,
        position    double precision NOT NULL,
        PRIMARY KEY (site, day, page, query)
    )""",
    # host is part of the PK because the Cloudflare token is account-scoped and
    # the account serves several unrelated sites. Without it, every site's "/"
    # collapses into one row.
    """CREATE TABLE IF NOT EXISTS cf_rum_daily (
        day          date    NOT NULL,
        host         text    NOT NULL DEFAULT '',
        path         text    NOT NULL,
        referer_host text    NOT NULL DEFAULT '',
        pageviews    integer NOT NULL,
        PRIMARY KEY (day, host, path, referer_host)
    )""",
    # Zone-scoped edge requests, client-facing only (requestSource eyeball),
    # by crawler class. Cloudflare keeps this dataset ~32 days; the table is
    # the durable history. See sources/cf_edge.py for why the filter matters.
    """CREATE TABLE IF NOT EXISTS cf_edge_daily (
        day          date    NOT NULL,
        host         text    NOT NULL,
        agent_class  text    NOT NULL,
        status_class text    NOT NULL,
        requests     integer NOT NULL,
        PRIMARY KEY (day, host, agent_class, status_class)
    )""",
    # Googlebot fetches by path class: "page fetches excluding robots.txt and
    # sitemaps" is the leading indicator for a new site's indexation.
    """CREATE TABLE IF NOT EXISTS cf_googlebot_paths_daily (
        day            date    NOT NULL,
        host           text    NOT NULL,
        path_class     text    NOT NULL,
        status_class   text    NOT NULL,
        requests       integer NOT NULL,
        distinct_paths integer NOT NULL DEFAULT 0,
        PRIMARY KEY (day, host, path_class, status_class)
    )""",
    """CREATE TABLE IF NOT EXISTS cf_pages_functions_daily (
        day         date    NOT NULL,
        project     text    NOT NULL,
        status      text    NOT NULL DEFAULT '',
        requests    integer NOT NULL,
        errors      integer NOT NULL DEFAULT 0,
        subrequests integer NOT NULL DEFAULT 0,
        PRIMARY KEY (day, project, status)
    )""",
    # Weekly URL Inspection sample; one row per inspected URL per sample day.
    # page_class is the configured sitemap prefix the URL was drawn for.
    """CREATE TABLE IF NOT EXISTS gsc_index_sample (
        sample_day       date        NOT NULL,
        site             text        NOT NULL,
        page_class       text        NOT NULL,
        url              text        NOT NULL,
        verdict          text        NOT NULL DEFAULT '',
        coverage_state   text        NOT NULL DEFAULT '',
        indexing_state   text        NOT NULL DEFAULT '',
        page_fetch_state text        NOT NULL DEFAULT '',
        robots_state     text        NOT NULL DEFAULT '',
        last_crawl       timestamptz,
        google_canonical text        NOT NULL DEFAULT '',
        PRIMARY KEY (sample_day, site, url)
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
        INSERT INTO gsc_daily (site, day, page, query, clicks, impressions, position)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (site, day, page, query) DO UPDATE SET
            clicks = EXCLUDED.clicks, impressions = EXCLUDED.impressions,
            position = EXCLUDED.position""",
    "cf_rum_daily": """
        INSERT INTO cf_rum_daily (day, host, path, referer_host, pageviews)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (day, host, path, referer_host) DO UPDATE SET
            pageviews = GREATEST(cf_rum_daily.pageviews, EXCLUDED.pageviews)""",
    "cf_edge_daily": """
        INSERT INTO cf_edge_daily (day, host, agent_class, status_class, requests)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (day, host, agent_class, status_class) DO UPDATE SET
            requests = GREATEST(cf_edge_daily.requests, EXCLUDED.requests)""",
    "cf_googlebot_paths_daily": """
        INSERT INTO cf_googlebot_paths_daily
            (day, host, path_class, status_class, requests, distinct_paths)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (day, host, path_class, status_class) DO UPDATE SET
            requests       = GREATEST(cf_googlebot_paths_daily.requests, EXCLUDED.requests),
            distinct_paths = GREATEST(cf_googlebot_paths_daily.distinct_paths, EXCLUDED.distinct_paths)""",
    "cf_pages_functions_daily": """
        INSERT INTO cf_pages_functions_daily (day, project, status, requests, errors, subrequests)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (day, project, status) DO UPDATE SET
            requests    = GREATEST(cf_pages_functions_daily.requests,    EXCLUDED.requests),
            errors      = GREATEST(cf_pages_functions_daily.errors,      EXCLUDED.errors),
            subrequests = GREATEST(cf_pages_functions_daily.subrequests, EXCLUDED.subrequests)""",
    # A re-inspection on the same sample day is a newer observation; overwrite.
    "gsc_index_sample": """
        INSERT INTO gsc_index_sample
            (sample_day, site, page_class, url, verdict, coverage_state, indexing_state,
             page_fetch_state, robots_state, last_crawl, google_canonical)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (sample_day, site, url) DO UPDATE SET
            page_class = EXCLUDED.page_class, verdict = EXCLUDED.verdict,
            coverage_state = EXCLUDED.coverage_state, indexing_state = EXCLUDED.indexing_state,
            page_fetch_state = EXCLUDED.page_fetch_state, robots_state = EXCLUDED.robots_state,
            last_crawl = EXCLUDED.last_crawl, google_canonical = EXCLUDED.google_canonical""",
}

# Idempotent, run after DDL. CREATE TABLE IF NOT EXISTS cannot reshape a table
# that already exists, so GSC's site dimension and RUM's host dimension need
# explicit migrations. Their defaults preserve uniqueness under the wider keys,
# so the primary-key swaps cannot fail on existing rows.
MIGRATIONS = [
    # Rows collected before multi-property support all came from jedarden.com.
    # Giving them that property before widening the PK preserves every row and
    # prevents a DevImprint page/query tuple from overwriting it.
    "ALTER TABLE gsc_daily ADD COLUMN IF NOT EXISTS site text NOT NULL DEFAULT 'sc-domain:jedarden.com'",
    """DO $$
       BEGIN
         IF NOT EXISTS (
           SELECT 1
           FROM pg_index i
           JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
           WHERE i.indrelid = 'gsc_daily'::regclass
             AND i.indisprimary
             AND a.attname = 'site'
         ) THEN
           ALTER TABLE gsc_daily DROP CONSTRAINT IF EXISTS gsc_daily_pkey;
           ALTER TABLE gsc_daily ADD PRIMARY KEY (site, day, page, query);
         END IF;
       END $$""",
    "ALTER TABLE cf_rum_daily ADD COLUMN IF NOT EXISTS host text NOT NULL DEFAULT ''",
    """DO $$
       BEGIN
         IF NOT EXISTS (
           SELECT 1
           FROM pg_index i
           JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
           WHERE i.indrelid = 'cf_rum_daily'::regclass
             AND i.indisprimary
             AND a.attname = 'host'
         ) THEN
           ALTER TABLE cf_rum_daily DROP CONSTRAINT IF EXISTS cf_rum_daily_pkey;
           ALTER TABLE cf_rum_daily ADD PRIMARY KEY (day, host, path, referer_host);
         END IF;
       END $$""",
]
