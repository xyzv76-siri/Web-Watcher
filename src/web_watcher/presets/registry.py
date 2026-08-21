"""Preset registry for Web-Watcher monitoring templates."""

from typing import Dict, List, Optional, Any
from urllib.parse import urlparse
from web_watcher.rule_models import WatcherRule, TargetConfig, ExtractorConfig, TriggerConfig, RoutingConfig


class PresetDefinition:
    """Monitoring preset definition."""

    def __init__(
        self,
        name: str,
        description: str,
        build_rule: callable,
    ):
        self.name = name
        self.description = description
        self.build_rule = build_rule

    def generate(self, url: str, **overrides: Any) -> WatcherRule:
        """Generate a WatcherRule from this preset."""
        return self.build_rule(url, **overrides)


# Preset builder functions

def _build_github_release(url: str, **overrides: Any) -> WatcherRule:
    """Build a rule for monitoring GitHub repository releases."""
    repo = overrides.get("repo")
    if repo:
        target_url = f"https://github.com/{repo}/releases/latest"
    else:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        if not path or "/" not in path.strip("/"):
            raise ValueError(
                "github_release preset requires a GitHub repository URL like https://github.com/owner/repo "
                "or use --repo owner/repo"
            )
        target_url = f"{parsed.scheme}://{parsed.netloc}{path}/releases/latest"

    rule_id = overrides.get("rule_id") or "github_release"
    name = overrides.get("name") or "GitHub Release Monitor"
    channel = overrides.get("channel") or "console"
    cooldown = overrides.get("cooldown") or "300s"
    interval = overrides.get("interval") or "15m"

    return WatcherRule(
        id=rule_id,
        name=name,
        target=TargetConfig(
            url=target_url,
            interval=interval,
            timeout=10.0,
        ),
        extractors=[
            ExtractorConfig(
                name="release_title",
                selector_type="css",
                selector="h1[data-view-component='true']",
                transforms=["text"],
            ),
        ],
        triggers=[
            TriggerConfig(
                type="content_change",
                field="release_title",
                importance="important",
                title_template="GitHub Release: {{release_title}}",
                body_template="Detected release page title change.\nURL: {url}\nTitle: {{release_title}}",
            ),
        ],
        routing=RoutingConfig(
            channels=[channel],
            cooldown=cooldown,
        ),
    )


def _build_blog_post(url: str, **overrides: Any) -> WatcherRule:
    """Build a rule for monitoring blog/news page title changes."""
    selector = overrides.get("selector") or "h1"
    interval = overrides.get("interval") or "15m"
    channel = overrides.get("channel") or "console"
    cooldown = overrides.get("cooldown") or "300s"
    rule_id = overrides.get("rule_id") or "blog_post"
    name = overrides.get("name") or "Blog Post Monitor"

    return WatcherRule(
        id=rule_id,
        name=name,
        target=TargetConfig(
            url=url,
            interval=interval,
            timeout=10.0,
        ),
        extractors=[
            ExtractorConfig(
                name="page_title",
                selector_type="css",
                selector=selector,
                transforms=["text"],
            ),
        ],
        triggers=[
            TriggerConfig(
                type="text_diff",
                field="page_title",
                importance="important",
                title_template="Blog update: {{page_title}}",
                body_template="Page title changed.\nURL: {url}\nNew title: {{page_title}}",
            ),
        ],
        routing=RoutingConfig(
            channels=[channel],
            cooldown=cooldown,
        ),
    )


def _build_price(url: str, **overrides: Any) -> WatcherRule:
    """Build a rule for monitoring price/numeric changes on a page."""
    selector = overrides.get("selector")
    if not selector:
        raise ValueError("price preset requires --selector")

    interval = overrides.get("interval") or "15m"
    channel = overrides.get("channel") or "console"
    cooldown = overrides.get("cooldown") or "300s"
    rule_id = overrides.get("rule_id") or "price_monitor"
    name = overrides.get("name") or "Price Monitor"

    return WatcherRule(
        id=rule_id,
        name=name,
        target=TargetConfig(
            url=url,
            interval=interval,
            timeout=10.0,
        ),
        extractors=[
            ExtractorConfig(
                name="price",
                selector_type="css",
                selector=selector,
                transforms=["text", "strip", "to_float"],
            ),
        ],
        triggers=[
            TriggerConfig(
                type="numeric_delta",
                field="price",
                condition="abs_delta > 0",
                importance="important",
                title_template="Price changed: {{old_value}} -> {{new_value}}",
                body_template="Price change detected.\nURL: {url}\nOld: {{old_value}}\nNew: {{new_value}}\nDelta: {{delta}}",
            ),
        ],
        routing=RoutingConfig(
            channels=[channel],
            cooldown=cooldown,
        ),
    )


# Registry

PRESETS: Dict[str, PresetDefinition] = {
    "github_release": PresetDefinition(
        name="GitHub Release",
        description="Monitor a GitHub repository's latest release page.",
        build_rule=_build_github_release,
    ),
    "blog_post": PresetDefinition(
        name="Blog Post",
        description="Monitor a blog or news page for title/content changes.",
        build_rule=_build_blog_post,
    ),
    "price": PresetDefinition(
        name="Price",
        description="Monitor a price or numeric value on a web page.",
        build_rule=_build_price,
    ),
}


def list_presets() -> List[PresetDefinition]:
    """Return all available presets."""
    return list(PRESETS.values())


def get_preset(name: str) -> PresetDefinition:
    """Get a preset by name."""
    if name not in PRESETS:
        raise KeyError(f"Unknown preset: {name}. Available: {', '.join(PRESETS.keys())}")
    return PRESETS[name]
