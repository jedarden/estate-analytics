"""Tiny stdlib HTTP helper — no requests dependency."""
import json, urllib.request

def get_json(url, headers=None, method="GET", body=None, timeout=30):
    req = urllib.request.Request(url, method=method,
        headers={"User-Agent": "estate-analytics", **(headers or {})},
        data=json.dumps(body).encode() if body is not None else None)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as f:
        raw = f.read()
        return json.loads(raw) if raw.strip() else {}

def get_text(url, headers=None, timeout=30):
    """Plain-text GET (sitemaps). Keeps the same identifying User-Agent: the
    sites sit behind Cloudflare's browser integrity check, which rejects
    Python's default UA but passes a named one (verified 2026-09-20)."""
    req = urllib.request.Request(url, headers={"User-Agent": "estate-analytics", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return f.read().decode("utf-8", "replace")
