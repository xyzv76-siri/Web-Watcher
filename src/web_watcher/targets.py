"""Watch target domain model and validation."""

from dataclasses import dataclass
from typing import Optional


SUPPORTED_TARGET_TYPES = frozenset({
    "github_repository",
    "official_website",
    "news_source",
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
    }:
        if not (
            target.locator.startswith("https://")
            or target.locator.startswith("http://")
        ):
            raise ValueError(
                "web target locator must use http:// or https://"
            )