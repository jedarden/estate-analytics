import pytest
from estate_analytics import config

def test_pairs():
    assert config.parse_pairs(" jedarden.com=230c , devimprint.com=4436,") == {
        "jedarden.com": "230c", "devimprint.com": "4436"}
    assert config.parse_pairs(None) == {} and config.parse_pairs("") == {}

def test_pairs_rejects_bare_items():
    with pytest.raises(ValueError):
        config.parse_pairs("jedarden.com")

def test_spec_preserves_order_and_groups():
    spec = config.parse_spec("halfonadouble.com=/stock/:stock,/learn/:learn; devimprint.com=/developers/:12")
    assert spec == {"halfonadouble.com": [("/stock/", "stock"), ("/learn/", "learn")],
                    "devimprint.com": [("/developers/", "12")]}

def test_spec_splits_on_last_colon_so_property_names_survive():
    """Search Console properties contain a colon (sc-domain:x); the value is
    after the LAST colon, so 'sc-domain:x=/stock/:12' parses."""
    assert config.parse_spec("sc-domain:halfonadouble.com=/stock/:12") == {
        "sc-domain:halfonadouble.com": [("/stock/", "12")]}

def test_spec_rejects_malformed():
    with pytest.raises(ValueError):
        config.parse_spec("host=/stock/")
    with pytest.raises(ValueError):
        config.parse_spec("/stock/:x")
