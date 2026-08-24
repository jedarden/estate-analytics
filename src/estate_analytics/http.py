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
