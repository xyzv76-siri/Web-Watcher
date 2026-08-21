import pytest
from web_watcher.presets import get_preset, list_presets, PRESETS
from web_watcher.rule_parser import RuleParser
from web_watcher.rule_models import WatcherRule


def test_list_presets_returns_all():
    presets = list_presets()
    assert len(presets) == 3
    names = {p.name for p in presets}
    assert "GitHub Release" in names
    assert "Blog Post" in names
    assert "Price" in names


def test_get_preset_known():
    preset = get_preset("github_release")
    assert preset.name == "GitHub Release"


def test_get_preset_unknown_raises():
    with pytest.raises(KeyError, match="Unknown preset"):
        get_preset("unknown_preset")


def test_github_release_generates_valid_rule():
    preset = get_preset("github_release")
    rule = preset.generate("https://github.com/owner/repo")
    assert isinstance(rule, WatcherRule)
    assert rule.id == "github_release"
    assert rule.target.url == "https://github.com/owner/repo/releases/latest"
    assert rule.target.interval == "15m"
    assert rule.target.timeout == 10.0
    assert len(rule.extractors) == 1
    assert rule.extractors[0].name == "release_title"
    assert rule.extractors[0].selector == "h1[data-view-component='true']"
    assert len(rule.triggers) == 1
    assert rule.triggers[0].type == "content_change"
    assert rule.routing.channels == ["console"]


def test_github_release_with_repo_override():
    preset = get_preset("github_release")
    rule = preset.generate("https://example.com", repo="owner/repo")
    assert rule.target.url == "https://github.com/owner/repo/releases/latest"


def test_blog_post_generates_valid_rule():
    preset = get_preset("blog_post")
    rule = preset.generate("https://example.com/blog")
    assert isinstance(rule, WatcherRule)
    assert rule.id == "blog_post"
    assert rule.target.url == "https://example.com/blog"
    assert rule.extractors[0].selector == "h1"
    assert rule.triggers[0].type == "text_diff"


def test_blog_post_with_custom_selector():
    preset = get_preset("blog_post")
    rule = preset.generate("https://example.com/blog", selector="h2.entry-title")
    assert rule.extractors[0].selector == "h2.entry-title"


def test_price_generates_valid_rule():
    preset = get_preset("price")
    rule = preset.generate("https://example.com/product/123", selector=".price")
    assert isinstance(rule, WatcherRule)
    assert rule.id == "price_monitor"
    assert rule.target.url == "https://example.com/product/123"
    assert rule.extractors[0].selector == ".price"
    assert "to_float" in rule.extractors[0].transforms
    assert rule.triggers[0].type == "numeric_delta"
    assert rule.triggers[0].condition == "abs_delta > 0"


def test_price_without_selector_raises():
    preset = get_preset("price")
    with pytest.raises(ValueError, match="price preset requires --selector"):
        preset.generate("https://example.com/product/123")


def test_preset_output_is_parseable_by_rule_parser():
    """Generated YAML must be loadable by existing RuleParser."""
    preset = get_preset("github_release")
    rule = preset.generate("https://github.com/owner/repo")

    from web_watcher.cli import _rule_to_yaml
    yaml_content = _rule_to_yaml(rule)

    ruleset = RuleParser.parse_yaml_str(yaml_content)
    assert len(ruleset.rules) == 1
    assert ruleset.rules[0].id == rule.id
    assert ruleset.rules[0].target.url == rule.target.url


def test_all_presets_generate_parseable_yaml():
    """All presets must produce YAML that RuleParser accepts."""
    from web_watcher.cli import _rule_to_yaml

    cases = [
        ("github_release", "https://github.com/owner/repo", {}),
        ("blog_post", "https://example.com/blog", {"selector": "h1"}),
        ("price", "https://example.com/product/123", {"selector": ".price"}),
    ]

    for preset_name, url, overrides in cases:
        preset = get_preset(preset_name)
        rule = preset.generate(url, **overrides)
        yaml_content = _rule_to_yaml(rule)
        ruleset = RuleParser.parse_yaml_str(yaml_content)
        assert len(ruleset.rules) == 1
        assert ruleset.rules[0].id == rule.id


def test_preset_output_is_deterministic():
    """Same preset + URL should produce identical YAML."""
    preset = get_preset("github_release")
    rule1 = preset.generate("https://github.com/owner/repo")
    rule2 = preset.generate("https://github.com/owner/repo")

    from web_watcher.cli import _rule_to_yaml
    assert _rule_to_yaml(rule1) == _rule_to_yaml(rule2)
