from web_watcher.dom_extractor import DOMExtractor
from web_watcher.rule_models import ExtractorConfig, ExtractionStatus


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
    cfg = ExtractorConfig(name="title", selector_type="css", selector="#main-header .title")
    res = DOMExtractor.extract(HTML_DOC, cfg)
    assert res.status == ExtractionStatus.FOUND
    assert res.value == "Product Pricing"


def test_extract_nested_descendant():
    cfg = ExtractorConfig(name="amount", selector_type="css", selector="div.pricing-card div.price-box span.amount")
    res = DOMExtractor.extract(HTML_DOC, cfg)
    assert res.status == ExtractionStatus.FOUND
    assert res.value == "79.00"


def test_extract_with_transforms():
    cfg = ExtractorConfig(
        name="pro_price",
        selector_type="css",
        selector="span.amount",
        transforms=["strip_tags", "to_float"],
    )
    res = DOMExtractor.extract(HTML_DOC, cfg)
    assert res.status == ExtractionStatus.FOUND
    assert res.value == 79.0


def test_extract_by_regex():
    cfg = ExtractorConfig(
        name="sku",
        selector_type="regex",
        selector=r"Pro Plan",
    )
    res = DOMExtractor.extract(HTML_DOC, cfg)
    assert res.status == ExtractionStatus.FOUND
    assert res.value == "Pro Plan"


def test_extract_missing_selector_returns_empty():
    cfg = ExtractorConfig(name="missing", selector_type="css", selector="#non-existent")
    res = DOMExtractor.extract(HTML_DOC, cfg)
    assert res.status == ExtractionStatus.SELECTOR_NOT_FOUND


def test_extract_raw_text():
    cfg = ExtractorConfig(
        name="raw",
        selector_type="raw",
        selector="",
    )
    res = DOMExtractor.extract(HTML_DOC, cfg)
    assert res.status == ExtractionStatus.FOUND
    assert "Product Pricing" in res.value
    assert "Pro Plan" in res.value


MULTI_HTML_DOC = """
<!DOCTYPE html>
<html>
<body>
    <div class="item">First</div>
    <div class="item">Second</div>
    <div class="item">Third</div>
</body>
</html>
"""


def test_extract_multiple_css_matches_returns_multiple_match():
    cfg = ExtractorConfig(name="items", selector_type="css", selector="div.item")
    res = DOMExtractor.extract(MULTI_HTML_DOC, cfg)
    assert res.status == ExtractionStatus.MULTIPLE_MATCH
    assert res.metadata["match_count"] == 3
    assert res.value is None
    assert "matched 3 elements" in res.error_message


def test_extract_multiple_css_matches_does_not_auto_select_first():
    cfg = ExtractorConfig(name="items", selector_type="css", selector="div.item")
    res = DOMExtractor.extract(MULTI_HTML_DOC, cfg)
    assert res.status == ExtractionStatus.MULTIPLE_MATCH
    assert res.value is None
    assert "First" not in str(res.value)
    assert "Second" not in str(res.value)
    assert "Third" not in str(res.value)
