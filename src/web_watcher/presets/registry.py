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
        tags=overrides.get("tags", ["github", "release"]),
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
        tags=overrides.get("tags", ["blog", "content"]),
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
        tags=overrides.get("tags", ["price", "ecommerce"]),
    )


def _build_noise_reduction(url: str, **overrides: Any) -> WatcherRule:
    """Build a rule optimized for noisy pages: precise selector + text_diff + conservative cooldown."""
    selector = overrides.get("selector") or "body"
    interval = overrides.get("interval") or "15m"
    channel = overrides.get("channel") or "console"
    cooldown = overrides.get("cooldown") or "600s"
    rule_id = overrides.get("rule_id") or "noise_reduction"
    name = overrides.get("name") or "Noise Reduction Monitor"

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
                name="content",
                selector_type="css",
                selector=selector,
                transforms=["text", "strip"],
            ),
        ],
        triggers=[
            TriggerConfig(
                type="text_diff",
                field="content",
                importance="important",
                title_template="Content changed: {{content}}",
                body_template="Detected text change on monitored page.\nURL: {url}\nContent: {{content}}",
            ),
        ],
        routing=RoutingConfig(
            channels=[channel],
            cooldown=cooldown,
        ),
        tags=overrides.get("tags", ["noise-reduction", "content"]),
    )


def _infer_product_page_selector(url: str) -> str:
    """Infer a default product page selector from common e-commerce patterns."""
    host = urlparse(url).netloc.lower()
    if "amazon." in host:
        return "#corePrice_feature_div .a-price-whole"
    if "ebay." in host:
        ".ux-image-magnify__image"
    if "shopify." in host:
        return ".product__price"
    return ".price, .product-price, [data-price], .price__current"


def _build_product_page(url: str, **overrides: Any) -> WatcherRule:
    """Build a rule for monitoring product pages with price and title extraction."""
    selector = overrides.get("selector") or _infer_product_page_selector(url)
    interval = overrides.get("interval") or "15m"
    channel = overrides.get("channel") or "console"
    cooldown = overrides.get("cooldown") or "300s"
    rule_id = overrides.get("rule_id") or "product_page"
    name = overrides.get("name") or "Product Page Monitor"

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
                name="product_title",
                selector_type="css",
                selector="h1, .product-title, .product__title",
                transforms=["text"],
            ),
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
                title_template="Product price changed: {{old_value}} -> {{new_value}}",
                body_template="Price change detected.\nURL: {url}\nProduct: {{product_title}}\nOld: {{old_value}}\nNew: {{new_value}}\nDelta: {{delta}}",
            ),
        ],
        routing=RoutingConfig(
            channels=[channel],
            cooldown=cooldown,
        ),
        tags=overrides.get("tags", ["product", "price", "ecommerce"]),
    )


def _infer_news_article_selector(url: str) -> str:
    """Infer a default news/article selector from common CMS patterns."""
    host = urlparse(url).netloc.lower()
    if "medium.com" in host:
        return "article h1"
    if "wordpress.com" in host or "/wp/" in url:
        return ".entry-title, h1"
    if "bbc." in host:
        return "h1.story-body"
    return "h1, article h1, .article-title"


def _build_news_article(url: str, **overrides: Any) -> WatcherRule:
    """Build a rule for monitoring news/article page changes."""
    selector = overrides.get("selector") or _infer_news_article_selector(url)
    interval = overrides.get("interval") or "15m"
    channel = overrides.get("channel") or "console"
    cooldown = overrides.get("cooldown") or "300s"
    rule_id = overrides.get("rule_id") or "news_article"
    name = overrides.get("name") or "News Article Monitor"

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
                name="article_title",
                selector_type="css",
                selector=selector,
                transforms=["text"],
            ),
            ExtractorConfig(
                name="article_body",
                selector_type="css",
                selector="article p, .article-body p, .entry-content p",
                transforms=["text", "strip"],
            ),
        ],
        triggers=[
            TriggerConfig(
                type="text_diff",
                field="article_title",
                importance="important",
                title_template="News update: {{article_title}}",
                body_template="Article changed.\nURL: {url}\nTitle: {{article_title}}\nBody: {{article_body}}",
            ),
        ],
        routing=RoutingConfig(
            channels=[channel],
            cooldown=cooldown,
        ),
        tags=overrides.get("tags", ["news", "article", "content"]),
    )


def _infer_status_page_selector(url: str) -> str:
    """Infer a default status page selector for common status page patterns."""
    host = urlparse(url).netloc.lower()
    if "status." in host:
        return ".status, .component-status, .incident-title"
    return ".status, .incident, .status-page__component"


def _build_status_page(url: str, **overrides: Any) -> WatcherRule:
    """Build a rule for monitoring service status pages."""
    selector = overrides.get("selector") or _infer_status_page_selector(url)
    interval = overrides.get("interval") or "5m"
    channel = overrides.get("channel") or "console"
    cooldown = overrides.get("cooldown") or "180s"
    rule_id = overrides.get("rule_id") or "status_page"
    name = overrides.get("name") or "Status Page Monitor"

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
                name="overall_status",
                selector_type="css",
                selector=selector,
                transforms=["text", "strip"],
            ),
        ],
        triggers=[
            TriggerConfig(
                type="text_diff",
                field="overall_status",
                importance="critical",
                title_template="Status change: {{overall_status}}",
                body_template="Service status changed.\nURL: {url}\nStatus: {{overall_status}}",
            ),
        ],
        routing=RoutingConfig(
            channels=[channel],
            cooldown=cooldown,
        ),
        tags=overrides.get("tags", ["status", "ops"]),
    )


def _infer_changelog_selector(url: str) -> str:
    """Infer a default changelog selector from common project doc patterns."""
    host = urlparse(url).netloc.lower()
    if "github.com" in host:
        return ".markdown-body h2, .markdown-body h3"
    if "gitlab.com" in host:
        return ".markdown-body h2, .markdown-body h3"
    return "h2, h3, .changelog__version, .release__title"


def _build_changelog(url: str, **overrides: Any) -> WatcherRule:
    """Build a rule for monitoring project changelog/release notes."""
    selector = overrides.get("selector") or _infer_changelog_selector(url)
    interval = overrides.get("interval") or "30m"
    channel = overrides.get("channel") or "console"
    cooldown = overrides.get("cooldown") or "600s"
    rule_id = overrides.get("rule_id") or "changelog"
    name = overrides.get("name") or "Changelog Monitor"

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
                name="latest_version",
                selector_type="css",
                selector=selector,
                transforms=["text", "strip"],
            ),
        ],
        triggers=[
            TriggerConfig(
                type="text_diff",
                field="latest_version",
                importance="important",
                title_template="New version: {{latest_version}}",
                body_template="Changelog updated.\nURL: {url}\nLatest: {{latest_version}}",
            ),
        ],
        routing=RoutingConfig(
            channels=[channel],
            cooldown=cooldown,
        ),
        tags=overrides.get("tags", ["changelog", "version"]),
    )


def _build_rss_feed(url: str, **overrides: Any) -> WatcherRule:
    """Build a rule for monitoring an RSS/Atom feed for new entries."""
    interval = overrides.get("interval") or "30m"
    channel = overrides.get("channel") or "console"
    cooldown = overrides.get("cooldown") or "300s"
    rule_id = overrides.get("rule_id") or "rss_feed"
    name = overrides.get("name") or "RSS Feed Monitor"

    return WatcherRule(
        id=rule_id,
        name=name,
        target=TargetConfig(
            url=url,
            interval=interval,
            timeout=10.0,
        ),
        extractors=[],
        triggers=[],
        routing=RoutingConfig(
            channels=[channel],
            cooldown=cooldown,
        ),
        tags=overrides.get("tags", ["rss", "feed"]),
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
    "noise_reduction": PresetDefinition(
        name="Noise Reduction",
        description="Monitor a noisy page with precise selector and conservative cooldown to reduce false positives.",
        build_rule=_build_noise_reduction,
    ),
    "product_page": PresetDefinition(
        name="Product Page",
        description="Monitor a product page for price and title changes with built-in e-commerce selectors.",
        build_rule=_build_product_page,
    ),
    "news_article": PresetDefinition(
        name="News Article",
        description="Monitor a news/article page for title and content changes.",
        build_rule=_build_news_article,
    ),
    "status_page": PresetDefinition(
        name="Status Page",
        description="Monitor a service status page for incident/status changes.",
        build_rule=_build_status_page,
    ),
    "changelog": PresetDefinition(
        name="Changelog",
        description="Monitor a project changelog or release notes for new versions.",
        build_rule=_build_changelog,
    ),
    "rss_feed": PresetDefinition(
        name="RSS Feed",
        description="Monitor an RSS/Atom feed for new entries.",
        build_rule=_build_rss_feed,
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
