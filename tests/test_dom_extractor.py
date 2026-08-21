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
    <div class="item"><span>First</span></div>
    <div class="item"><span>Second</span></div>
    <div class="item"><span>Third</span></div>
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


def test_extract_with_diff_scope_narrows_content():
    cfg = ExtractorConfig(
        name="price",
        selector_type="css",
        selector="div.pricing-card",
        scope_selector=".price-box",
    )
    res = DOMExtractor.extract(HTML_DOC, cfg)
    assert res.status == ExtractionStatus.FOUND
    assert "$" in res.value
    assert "79.00" in res.value
    assert "/month" in res.value
    assert "In Stock" not in res.value
    assert res.metadata.get("scope_selector") == ".price-box"


def test_extract_with_diff_scope_miss_returns_not_found():
    cfg = ExtractorConfig(
        name="price",
        selector_type="css",
        selector="div.pricing-card",
        scope_selector=".non-existent",
    )
    res = DOMExtractor.extract(HTML_DOC, cfg)
    assert res.status == ExtractionStatus.SELECTOR_NOT_FOUND
    assert res.metadata.get("scope_miss") is True
    assert "matched 0 elements" in (res.error_message or "")


def test_extract_without_scope_keeps_existing_behavior():
    cfg = ExtractorConfig(
        name="price",
        selector_type="css",
        selector="div.pricing-card",
    )
    res = DOMExtractor.extract(HTML_DOC, cfg)
    assert res.status == ExtractionStatus.FOUND
    assert "Pro Plan" in res.value
    assert "$" in res.value
    assert "In Stock" in res.value


def test_extract_with_scope_and_multiple_elements_merges():
    cfg = ExtractorConfig(
        name="price",
        selector_type="css",
        selector="div.pricing-card",
        scope_selector="span",
    )
    res = DOMExtractor.extract(HTML_DOC, cfg)
    assert res.status == ExtractionStatus.FOUND
    assert "$" in res.value
    assert "79.00" in res.value
    assert "/month" in res.value
    assert "Pro Plan" in res.value
    assert res.metadata.get("merged_count") == 4


def test_extract_with_invalid_scope_selector_returns_transform_error():
    cfg = ExtractorConfig(
        name="price",
        selector_type="css",
        selector="div.pricing-card",
        scope_selector="##invalid",
    )
    res = DOMExtractor.extract(HTML_DOC, cfg)
    assert res.status == ExtractionStatus.TRANSFORM_ERROR
    assert "Invalid scope_selector" in (res.error_message or "")


def test_extract_scope_on_multiple_elements_within_extractor_result():
    """selector matches multiple elements, scope narrows each."""
    cfg = ExtractorConfig(
        name="items",
        selector_type="css",
        selector="div.item",
        scope_selector="span",
    )
    res = DOMExtractor.extract(MULTI_HTML_DOC, cfg)
    assert res.status == ExtractionStatus.FOUND
    assert "First" in res.value
    assert "Second" in res.value
    assert "Third" in res.value
    assert res.metadata.get("merged_count") == 3
