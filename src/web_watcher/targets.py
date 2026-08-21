"""Watch target domain model and validation."""

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse


SUPPORTED_TARGET_TYPES = frozenset({
    "github_repository",
    "official_website",
    "news_source",
    "web",
})


@dataclass(frozen=True)
class WatchTarget:
    key: str
    target_type: str
    name: str
    locator: str
    enabled: bool = True
    priority: int = 50
    poll_interval_seconds: Optional[int] = None


def validate_watch_target(target: WatchTarget) -> None:
    if not target.key.strip():
        raise ValueError("target key must not be empty")

    if target.target_type not in SUPPORTED_TARGET_TYPES:
        raise ValueError(
            f"unsupported target type: {target.target_type}"
        )

    if not target.name.strip():
        raise ValueError("target name must not be empty")

    if not target.locator.strip():
        raise ValueError("target locator must not be empty")

    if not 0 <= target.priority <= 100:
        raise ValueError("priority must be between 0 and 100")

    if target.poll_interval_seconds is not None:
        if target.poll_interval_seconds <= 0:
            raise ValueError(
                "poll interval must be greater than zero"
            )


def _validate_url(url: str) -> None:
    """Validate that a URL is well-formed, has a scheme, has a hostname, and only uses http/https."""
    if not url or not url.strip():
        raise ValueError("URL must not be empty")

    try:
        parsed = urlparse(url.strip())
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid URL: {exc}") from exc

    if not parsed.scheme:
        raise ValueError("URL must have a scheme")

    if parsed.scheme not in {"http", "https"}:
        raise ValueError(
            f"URL scheme must be http or https, got: {parsed.scheme}"
        )

    if not parsed.hostname:
        raise ValueError("URL must have a hostname")


def validate_target_url_policy(target: WatchTarget) -> None:
    """
    Validate locator policy without performing network access.

    This phase only establishes the contract.
    Actual network safety belongs to the future fetch layer.
    """
    if target.target_type == "github_repository":
        if "/" not in target.locator:
            raise ValueError(
                "github repository locator must be owner/repository"
            )

    if target.target_type in {
        "official_website",
        "news_source",
        "web",
    }:
        _validate_url(target.locator)


def validate_selector(selector_type: str, selector: str) -> None:
    """Validate basic selector configuration format.

    - selector_type must be 'css' or 'xpath'.
    - selector must be a non-empty string.
    """
    if not selector_type or not selector_type.strip():
        raise ValueError("selector type must not be empty")

    if selector_type not in {"css", "xpath"}:
        raise ValueError(
            f"selector type must be 'css' or 'xpath', got: {selector_type}"
        )

    if not selector or not selector.strip():
        raise ValueError("selector must not be empty")