from web_watcher.rule_evaluator import RuleEvaluator, EvaluationResult, TriggeredEvent
from web_watcher.rule_models import WatcherRule, TargetConfig, ExtractorConfig, TriggerConfig, RoutingConfig


HTML_DOC = """
<!DOCTYPE html>
<html>
<body>
    <div class="price-box">
        <span class="amount">99.00</span>
    </div>
</body>
</html>
"""


def _make_rule() -> WatcherRule:
    return WatcherRule(
        id="test_rule",
        name="Test Rule",
        target=TargetConfig(url="https://example.com", interval="5m"),
        extractors=[
            ExtractorConfig(name="price", selector_type="css", selector="span.amount", transforms=["strip_tags", "to_float"])
        ],
        triggers=[
            TriggerConfig(type="numeric_delta", field="price", condition="abs_delta > 0.01", importance="important")
        ],
        routing=RoutingConfig(channels=["console"], cooldown="300s"),
    )


def test_evaluate_numeric_delta_trigger():
    rule = _make_rule()
    result = RuleEvaluator.evaluate(rule, HTML_DOC, old_values={"price": 89.00})
    assert result.is_triggered is True
    assert len(result.triggered_events) == 1
    ev = result.triggered_events[0]
    assert ev.field_name == "price"
    assert ev.old_value == 89.00
    assert ev.new_value == 99.00
    assert ev.importance == "important"


def test_evaluate_no_trigger_when_within_threshold():
    rule = _make_rule()
    # Same value => delta = 0.0, condition abs_delta > 0.01 is False
    result = RuleEvaluator.evaluate(rule, HTML_DOC, old_values={"price": 99.00})
    assert result.is_triggered is False
    assert len(result.triggered_events) == 0


def test_evaluate_text_diff_trigger():
    rule = WatcherRule(
        id="text_rule",
        name="Text Rule",
        target=TargetConfig(url="https://example.com"),
        extractors=[ExtractorConfig(name="status", selector_type="css", selector="p.status")],
        triggers=[TriggerConfig(type="text_diff", field="status")],
        routing=RoutingConfig(),
    )
    html_v1 = "<p class='status'>In Stock</p>"
    html_v2 = "<p class='status'>Out of Stock</p>"
    r1 = RuleEvaluator.evaluate(rule, html_v1)
    r2 = RuleEvaluator.evaluate(rule, html_v2, old_values=r1.extracted_values)
    assert r2.is_triggered is True
    assert r2.triggered_events[0].trigger_type == "text_diff"


def test_evaluate_regex_match_trigger():
    rule = WatcherRule(
        id="regex_rule",
        name="Regex Rule",
        target=TargetConfig(url="https://example.com"),
        extractors=[ExtractorConfig(name="sku", selector_type="css", selector="span.sku")],
        triggers=[TriggerConfig(type="regex_match", field="sku", condition=r"SKU-\d+")],
        routing=RoutingConfig(),
    )
    html = "<span class='sku'>SKU-1234</span>"
    result = RuleEvaluator.evaluate(rule, html, old_values={"sku": "OLD-VALUE"})
    assert result.is_triggered is True


def test_render_template_with_context():
    rule = _make_rule()
    result = RuleEvaluator.evaluate(rule, HTML_DOC, old_values={"price": 89.00})
    ev = result.triggered_events[0]
    assert "89.0" in ev.title
    assert "99.0" in ev.title


def test_evaluate_returns_extracted_values():
    rule = _make_rule()
    result = RuleEvaluator.evaluate(rule, HTML_DOC)
    assert "price" in result.extracted_values
    assert result.extracted_values["price"] == 99.00
