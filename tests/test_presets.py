import pytest
from web_watcher.presets import get_preset, list_presets, PRESETS
from web_watcher.rule_parser import RuleParser
from web_watcher.rule_models import WatcherRule


def test_list_presets_returns_all():
    presets = list_presets()
    assert len(presets) == 8
    names = {p.name for p in presets}
    assert "GitHub Release" in names
    assert "Blog Post" in names
    assert "Price" in names
    assert "Noise Reduction" in names
    assert "Product Page" in names
    assert "News Article" in names
    assert "Status Page" in names
    assert "Changelog" in names


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


def test_noise_reduction_generates_valid_rule():
    preset = get_preset("noise_reduction")
    rule = preset.generate("https://example.com/noisy", selector=".main")
    assert isinstance(rule, WatcherRule)
    assert rule.id == "noise_reduction"
    assert rule.target.url == "https://example.com/noisy"
    assert rule.target.interval == "15m"
    assert rule.extractors[0].selector == ".main"
    assert "strip" in rule.extractors[0].transforms
    assert rule.triggers[0].type == "text_diff"
    assert rule.triggers[0].field == "content"
    assert rule.routing.cooldown == "600s"


def test_noise_reduction_default_selector_is_body():
    preset = get_preset("noise_reduction")
    rule = preset.generate("https://example.com/noisy")
    assert rule.extractors[0].selector == "body"


def test_product_page_generates_valid_rule():
    preset = get_preset("product_page")
    rule = preset.generate("https://example.com/product/123")
    assert isinstance(rule, WatcherRule)
    assert rule.id == "product_page"
    assert rule.target.url == "https://example.com/product/123"
    assert rule.target.interval == "15m"
    assert len(rule.extractors) == 2
    assert rule.extractors[0].name == "product_title"
    assert rule.extractors[1].name == "price"
    assert rule.triggers[0].type == "numeric_delta"
    assert rule.triggers[0].field == "price"
    assert rule.routing.cooldown == "300s"


def test_product_page_infers_selector_for_amazon():
    preset = get_preset("product_page")
    rule = preset.generate("https://www.amazon.com/dp/B00123")
    assert rule.extractors[1].selector == "#corePrice_feature_div .a-price-whole"


def test_news_article_generates_valid_rule():
    preset = get_preset("news_article")
    rule = preset.generate("https://example.com/news/article")
    assert isinstance(rule, WatcherRule)
    assert rule.id == "news_article"
    assert rule.target.url == "https://example.com/news/article"
    assert len(rule.extractors) == 2
    assert rule.extractors[0].name == "article_title"
    assert rule.extractors[1].name == "article_body"
    assert rule.triggers[0].type == "text_diff"
    assert rule.triggers[0].field == "article_title"
    assert rule.routing.cooldown == "300s"


def test_status_page_generates_valid_rule():
    preset = get_preset("status_page")
    rule = preset.generate("https://status.example.com")
    assert isinstance(rule, WatcherRule)
    assert rule.id == "status_page"
    assert rule.target.url == "https://status.example.com"
    assert rule.target.interval == "5m"
    assert rule.routing.cooldown == "180s"
    assert rule.triggers[0].importance == "critical"
    assert rule.triggers[0].type == "text_diff"


def test_changelog_generates_valid_rule():
    preset = get_preset("changelog")
    rule = preset.generate("https://github.com/owner/repo/blob/main/CHANGELOG.md")
    assert isinstance(rule, WatcherRule)
    assert rule.id == "changelog"
    assert rule.target.url == "https://github.com/owner/repo/blob/main/CHANGELOG.md"
    assert rule.target.interval == "30m"
    assert rule.routing.cooldown == "600s"
    assert rule.triggers[0].type == "text_diff"
    assert rule.triggers[0].field == "latest_version"


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
        ("noise_reduction", "https://example.com/noisy", {"selector": "body"}),
        ("product_page", "https://example.com/product/123", {}),
        ("news_article", "https://example.com/news/article", {}),
        ("status_page", "https://status.example.com", {}),
        ("changelog", "https://github.com/owner/repo/blob/main/CHANGELOG.md", {}),
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


def test_new_presets_have_tags():
    """New presets should include default tags for grouping."""
    new_presets = ["product_page", "news_article", "status_page", "changelog"]
    for preset_name in new_presets:
        preset = get_preset(preset_name)
        rule = preset.generate("https://example.com")
        assert rule.tags, f"{preset_name} should have default tags"
