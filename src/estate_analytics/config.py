"""Parsers for the small structured env values the collector takes.

Grammar shared by CF_ZONES, CF_PATH_CLASSES and GSC_INDEX_SAMPLE:

    key=a:b,a:b;key=a:b            (spec:  {key: [(a, b), ...]})
    key=value,key=value            (pairs: {key: value})

Whitespace around any token is ignored; empty items are skipped. Values are
returned as strings -- callers convert. Kept out of main.py so the grammar is
testable without a database or credentials.
"""

def parse_pairs(raw):
    """'jedarden.com=230c…,devimprint.com=4436…' -> {'jedarden.com': '230c…', …}"""
    out = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"expected key=value, got {item!r}")
        k, v = (s.strip() for s in item.split("=", 1))
        if k and v:
            out[k] = v
    return out

def parse_spec(raw):
    """'h=/stock/:stock,/learn/:learn;h2=/developers/:developers'
    -> {'h': [('/stock/', 'stock'), ('/learn/', 'learn')], 'h2': [...]}

    Order within a key is preserved: path classification is first-match."""
    out = {}
    for group in (raw or "").split(";"):
        group = group.strip()
        if not group:
            continue
        if "=" not in group:
            raise ValueError(f"expected key=a:b,..., got {group!r}")
        key, items = (s.strip() for s in group.split("=", 1))
        entries = []
        for item in items.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" not in item:
                raise ValueError(f"expected a:b in {key!r}, got {item!r}")
            a, b = (s.strip() for s in item.rsplit(":", 1))
            entries.append((a, b))
        if key:
            out[key] = entries
    return out
