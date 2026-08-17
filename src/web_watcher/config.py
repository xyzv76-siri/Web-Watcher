"""Configuration loading and validation."""

import json
from pathlib import Path

from .targets import (
    WatchTarget,
    validate_watch_target,
    validate_target_url_policy,
)


class ConfigError(ValueError):
    """Raised when watcher configuration is invalid."""


def load_config(path: str | Path) -> list[WatchTarget]:
    config_path = Path(path)

    try:
        raw = json.loads(
            config_path.read_text(encoding="utf-8")
        )
    except FileNotFoundError as exc:
        raise ConfigError(
            f"config file not found: {config_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"invalid JSON: {config_path}"
        ) from exc

    if not isinstance(raw, dict):
        raise ConfigError("config root must be an object")

    if raw.get("version") != 1:
        raise ConfigError("unsupported config version")

    targets = raw.get("watch_targets")

    if not isinstance(targets, list):
        raise ConfigError(
            "watch_targets must be a list"
        )

    result = []

    for index, item in enumerate(targets):
        if not isinstance(item, dict):
            raise ConfigError(
                f"watch_targets[{index}] must be an object"
            )

        try:
            target = WatchTarget(
                key=item["key"],
                target_type=item["target_type"],
                name=item["name"],
                locator=item["locator"],
                enabled=item.get("enabled", True),
                priority=item.get("priority", 50),
                poll_interval_seconds=item.get("poll_interval_seconds"),
            )
            validate_watch_target(target)
            validate_target_url_policy(target)
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(
                f"watch_targets[{index}] invalid: {exc}"
            ) from exc

        result.append(target)

    keys = [target.key for target in result]
    if len(keys) != len(set(keys)):
        raise ConfigError("watch target keys must be unique")

    return result