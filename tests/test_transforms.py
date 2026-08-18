import pytest
from web_watcher.transforms import (
    strip_tags,
    to_float,
    to_int,
    apply_transform,
    apply_transforms,
    TransformError,
)


def test_strip_tags():
    html_sample = "<div class='price'><b>$99.99</b> / mo</div>"
    assert strip_tags(html_sample) == "$99.99 / mo"


def test_to_float_and_int():
    assert to_float("$1,234.50") == 1234.50
    assert to_float(" -42.8% ") == -42.8
    assert to_int("Count: 1,500 units") == 1500
    assert to_int(99.9) == 99


def test_regex_transform():
    text = "Product SKU: SKU-9821-X in stock"
    assert apply_transform(text, "regex:SKU-(\\d+)-X") == "9821"
    assert apply_transform(text, "regex:in stock") == "in stock"


def test_hash_transform():
    h = apply_transform("hello world", "hash")
    assert len(h) == 64
    assert h == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_transform_chain():
    raw = "  <span> PRICE: $199.95 </span> "
    chain = ["strip_tags", "trim", "regex:\\$(\\d+\\.\\d+)", "to_float"]
    assert apply_transforms(raw, chain) == 199.95


def test_invalid_numeric_transform_raises():
    with pytest.raises(TransformError, match="Cannot convert"):
        to_float("no numbers here")
