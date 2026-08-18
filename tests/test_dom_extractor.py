from web_watcher.dom_extractor import DOMExtractor
from web_watcher.rule_models import ExtractorConfig


HTML_DOC = """
<!DOCTYPE html>
<html>
<body>
    <header id="main-header">
        <h1 class="title brand">Product Pricing</h1>
    </header>
    <div class="container">
        <div class="pricing-card" id="pro-card">
            <span class="plan-name">Pro Plan</span>
            <div class="price-box">
                <span class="currency">$</span>
                <span class="amount">79.00</span>
                <span class="period">/month</span>
            </div>
            <p class="status badge-success">In Stock</p>
        </div>
    </div>
</body>
</html>
"""


def test_extract_by_id_and_class():
    res = DOMExtractor.extract_by_css(HTML_DOC, "#main-header .title")
    assert res == "Product Pricing"


def test_extract_nested_descendant():
    res = DOMExtractor.extract_by_css(HTML_DOC, "div.pricing-card div.price-box span.amount")
    assert res == "79.00"


def test_extract_with_transforms():
    cfg = ExtractorConfig(
        name="pro_price",
        selector_type="css",
        selector="div.price-box",
        transforms=["strip_tags", "regex:\\$(\\d+\\.\\d+)"],
    )
    res = DOMExtractor.extract(HTML_DOC, cfg)
    assert res == "79.00"


def test_extract_by_regex():
    cfg = ExtractorConfig(
        name="sku",
        selector_type="regex",
        selector=r"Pro Plan",
    )
    res = DOMExtractor.extract(HTML_DOC, cfg)
    assert res == "Pro Plan"


def test_extract_missing_selector_returns_empty():
    res = DOMExtractor.extract_by_css(HTML_DOC, "#non-existent")
    assert res == ""


def test_extract_raw_text():
    cfg = ExtractorConfig(
        name="raw",
        selector_type="text",
        selector="",
    )
    res = DOMExtractor.extract(HTML_DOC, cfg)
    assert "Product Pricing" in res
    assert "Pro Plan" in res
