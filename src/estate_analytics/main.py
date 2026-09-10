"""Daily collection loop as a long-lived process (Deployment with an internal
scheduler — never a k8s CronJob in this estate).

Schedule: runs at RUN_AT_UTC_HOUR daily; also runs at startup when the last
successful run is older than CATCHUP_AFTER_HOURS (self-heal after downtime).
Sources with absent credentials are skipped, not fatal.

  /health -> 200 as soon as the process is up (liveness)
  /ready  -> 503 until the schema is ensured and the loop is running (readiness)
"""
import datetime as dt, http.server, json, os, threading, time, traceback
from . import store, publish as publish_mod
from .sources import github_traffic, gsc, cf_rum

STATE = {"started": False, "collected": False, "last": None}

def probe(path, state=None):
    """Map a probe path to (status code, payload).

    Readiness means the process is functional -- schema ensured, loop running --
    NOT that it has already collected. A pod deployed outside its run window has
    nothing legitimately to do for hours, so gating readiness on a completed
    cycle marks a healthy collector NotReady and trips ProgressDeadlineExceeded
    on every deploy that lands outside that window. Whether a cycle has run is
    reported in the payload instead, where it is information rather than an
    availability signal.
    """
    s = STATE if state is None else state
    if path == "/health":
        code = 200
    elif path == "/ready":
        code = 200 if s["started"] else 503
    else:
        code = 404
    return code, {"started": s["started"], "collected": s["collected"], "last": s["last"]}

def _env(name, default=None):
    v = os.environ.get(name, default)
    return v if v not in ("", None) else default

def run_cycle(dsn):
    results = {}
    gh_token = _env("GITHUB_TOKEN")
    if gh_token:
        _run(dsn, results, "github_traffic",
             lambda: github_traffic.collect(gh_token, _env("GITHUB_OWNER", "jedarden")))
    else:
        print("github_traffic: no GITHUB_TOKEN, skipped", flush=True)
    sa = _env("GSC_SA_JSON")
    if sa:
        _run(dsn, results, "gsc",
             lambda: gsc.collect(sa, _env("GSC_SITE", "sc-domain:jedarden.com"),
                                 days=int(_env("GSC_DAYS", "7"))))
    else:
        print("gsc: no GSC_SA_JSON, skipped", flush=True)
    cf = _env("CF_ANALYTICS_TOKEN")
    if cf:
        _run(dsn, results, "cf_rum",
             lambda: cf_rum.collect(cf, _env("CF_ACCOUNT_ID", ""),
                                    days=int(_env("CF_DAYS", "3"))))
    else:
        print("cf_rum: no CF_ANALYTICS_TOKEN, skipped", flush=True)
    _publish_dashboard(dsn, results)
    return results


def _publish_dashboard(dsn, results):
    """Push the dashboard.ardenone.com panel datasets to the Garage bucket.

    Absent credentials skip the publish rather than failing the cycle -- the
    collection legs are the product, the panel is a view of them. A publish
    failure is likewise logged and swallowed: it must never cost a cycle whose
    data already landed in Postgres.
    """
    bucket = _env("DEST_S3_BUCKET")
    key_id = _env("DEST_S3_ACCESS_KEY_ID")
    if not (bucket and key_id):
        print("publish: no DEST_S3_BUCKET/ACCESS_KEY_ID, skipped", flush=True)
        return
    try:
        import boto3
        from botocore.config import Config
        import psycopg
        s3 = boto3.client(
            "s3",
            endpoint_url=_env("DEST_S3_ENDPOINT"),
            aws_access_key_id=key_id,
            aws_secret_access_key=_env("DEST_S3_SECRET_ACCESS_KEY"),
            region_name=_env("DEST_S3_REGION", "garage"),
            config=Config(s3={"addressing_style": _env("DEST_S3_ADDRESSING_STYLE", "path")}),
        )
        with psycopg.connect(dsn) as conn:
            n = publish_mod.publish(conn, s3, bucket, _env("DEST_S3_PREFIX", "estate-analytics"))
        store.log_run(dsn, "publish", "ok", n)
        print(f"publish: wrote {n} objects", flush=True)
    except Exception as e:
        store.log_run(dsn, "publish", "error", 0, str(e))
        print(f"publish: FAILED ({e})", flush=True)
        traceback.print_exc()

def _run(dsn, results, name, fn):
    try:
        rows = store.upsert(dsn, fn())
        store.log_run(dsn, name, "ok", rows)
        results[name] = rows
        print(f"{name}: upserted {rows} rows", flush=True)
    except Exception as e:
        store.log_run(dsn, name, "error", 0, str(e))
        results[name] = f"error: {e}"
        traceback.print_exc()

class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        code, payload = probe(self.path)
        self.send_response(code)
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())
    def log_message(self, *a):
        pass

def main():
    dsn = os.environ["DATABASE_URL"]
    run_at = int(_env("RUN_AT_UTC_HOUR", "5"))
    catchup_h = float(_env("CATCHUP_AFTER_HOURS", "26"))
    threading.Thread(
        target=lambda: http.server.HTTPServer(("", 8080), Health).serve_forever(),
        daemon=True).start()
    store.ensure_schema(dsn)
    STATE["started"] = True
    print("schema ensured; entering loop", flush=True)
    last_run_date = None
    while True:
        now = dt.datetime.now(dt.timezone.utc)
        age = store.last_success_age_hours(dsn)
        due_daily = now.hour == run_at and last_run_date != now.date()
        due_catchup = (age is None or age > catchup_h) and last_run_date != now.date()
        if due_daily or due_catchup:
            print(f"cycle start ({'daily' if due_daily else 'catch-up'}) {now.isoformat()}", flush=True)
            results = run_cycle(dsn)
            last_run_date = now.date()
            STATE["last"] = {"at": now.isoformat(), "results": {k: str(v) for k, v in results.items()}}
            if any(isinstance(v, int) for v in results.values()):
                STATE["collected"] = True
        time.sleep(300)

if __name__ == "__main__":
    main()
