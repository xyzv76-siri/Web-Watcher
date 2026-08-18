import pytest
from web_watcher.rule_parser import RuleParser, RuleParseError


VALID_YAML = """
version: "1.0"
rules:
  - id: "aws_pricing"
    name: "AWS EC2 定价"
    target:
      url: "https://aws.amazon.com/ec2/pricing/"
      interval: "10m"
      timeout: 15
      headers:
        User-Agent: "WebWatcher/1.0"
    extractors:
      - name: "hourly_rate"
        selector_type: "css"
        selector: "div.pricing .price"
        transforms:
          - "strip_tags"
          - "to_float"
    triggers:
      - type: "numeric_delta"
        field: "hourly_rate"
        condition: "abs_delta > 0.01"
        importance: "critical"
    routing:
      channels: ["slack", "lark"]
      cooldown: "600s"
"""


def test_parse_valid_yaml():
    ruleset = RuleParser.parse_yaml_str(VALID_YAML)
    assert ruleset.version == "1.0"
    assert len(ruleset.rules) == 1

    rule = ruleset.rules[0]
    assert rule.id == "aws_pricing"
    assert rule.target.url == "https://aws.amazon.com/ec2/pricing/"
    assert rule.target.timeout == 15.0
    assert len(rule.extractors) == 1
    assert rule.extractors[0].name == "hourly_rate"
    assert rule.extractors[0].transforms == ["strip_tags", "to_float"]
    assert len(rule.triggers) == 1
    assert rule.triggers[0].importance == "critical"
    assert rule.routing.channels == ["slack", "lark"]


def test_parse_missing_url_raises_error():
    invalid_yaml = """
rules:
  - id: "bad_rule"
    target:
      interval: "5m"
"""
    with pytest.raises(RuleParseError, match="missing required target.url"):
        RuleParser.parse_yaml_str(invalid_yaml)


def test_parse_invalid_extractor_raises_error():
    invalid_yaml = """
rules:
  - id: "bad_extractor"
    target:
      url: "https://example.com"
    extractors:
      - selector_type: "css"
"""
    with pytest.raises(RuleParseError, match="extractor must have 'name' and 'selector'"):
        RuleParser.parse_yaml_str(invalid_yaml)


def test_parse_invalid_trigger_raises_error():
    invalid_yaml = """
rules:
  - id: "bad_trigger"
    target:
      url: "https://example.com"
    triggers:
      - importance: "critical"
"""
    with pytest.raises(RuleParseError, match="trigger must have 'type' and 'field'"):
        RuleParser.parse_yaml_str(invalid_yaml)


def test_parse_defaults_applied():
    minimal_yaml = """
rules:
  - target:
      url: "https://example.com"
"""
    ruleset = RuleParser.parse_yaml_str(minimal_yaml)
    rule = ruleset.rules[0]
    assert rule.id == "rule_1"
    assert rule.target.interval == "15m"
    assert rule.routing.channels == ["console"]
    assert rule.routing.cooldown == "300s"


def test_parse_file(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text(VALID_YAML, encoding="utf-8")
    ruleset = RuleParser.parse_file(f)
    assert len(ruleset.rules) == 1
    assert ruleset.rules[0].id == "aws_pricing"
