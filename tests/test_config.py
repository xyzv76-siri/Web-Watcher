"""Tests for configuration loading and validation."""

import json

import pytest

from web_watcher.config import ConfigError, load_config


def _write_config(tmp_path, data):
    path = tmp_path / "watcher.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestLoadConfig:

    def test_loads_empty_config(self, tmp_path):
        path = _write_config(tmp_path, {"version": 1, "watch_targets": []})
        targets = load_config(path)
        assert targets == []

    def test_loads_single_target(self, tmp_path):
        data = {
            "version": 1,
            "watch_targets": [
                {
                    "key": "github:openai/gpt",
                    "target_type": "github_repository",
                    "name": "GPT",
                    "locator": "https://github.com/openai/gpt",
                }
            ],
        }
        path = _write_config(tmp_path, data)
        targets = load_config(path)

        assert len(targets) == 1
        assert targets[0].key == "github:openai/gpt"
        assert targets[0].priority == 50
        assert targets[0].enabled is True

    def test_loads_default_priority_and_enabled(self, tmp_path):
        data = {
            "version": 1,
            "watch_targets": [
                {
                    "key": "a",
                    "target_type": "github_repository",
                    "name": "A",
                    "locator": "https://a",
                }
            ],
        }
        targets = load_config(_write_config(tmp_path, data))
        assert targets[0].priority == 50
        assert targets[0].enabled is True

    def test_loads_custom_priority_and_poll(self, tmp_path):
        data = {
            "version": 1,
            "watch_targets": [
                {
                    "key": "b",
                    "target_type": "official_website",
                    "name": "B",
                    "locator": "https://b",
                    "priority": 80,
                    "enabled": False,
                    "poll_interval_seconds": 3600,
                }
            ],
        }
        targets = load_config(_write_config(tmp_path, data))
        t = targets[0]
        assert t.priority == 80
        assert t.enabled is False
        assert t.poll_interval_seconds == 3600

    def test_loads_multiple_targets(self, tmp_path):
        data = {
            "version": 1,
            "watch_targets": [
                {
                    "key": "a",
                    "target_type": "github_repository",
                    "name": "A",
                    "locator": "https://a",
                },
                {
                    "key": "b",
                    "target_type": "news_source",
                    "name": "B",
                    "locator": "https://b/feed",
                },
            ],
        }
        targets = load_config(_write_config(tmp_path, data))
        assert len(targets) == 2


class TestConfigErrorCases:

    def test_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "missing.json")

    def test_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{bad json", encoding="utf-8")
        with pytest.raises(ConfigError, match="invalid JSON"):
            load_config(path)

    def test_non_object_root(self, tmp_path):
        path = _write_config(tmp_path, [])
        with pytest.raises(ConfigError, match="object"):
            load_config(path)

    def test_unsupported_version(self, tmp_path):
        path = _write_config(tmp_path, {"version": 2, "watch_targets": []})
        with pytest.raises(ConfigError, match="version"):
            load_config(path)

    def test_missing_version(self, tmp_path):
        path = _write_config(tmp_path, {"watch_targets": []})
        with pytest.raises(ConfigError, match="version"):
            load_config(path)

    def test_watch_targets_not_list(self, tmp_path):
        path = _write_config(tmp_path, {"version": 1, "watch_targets": "not-a-list"})
        with pytest.raises(ConfigError, match="list"):
            load_config(path)

    def test_target_item_not_dict(self, tmp_path):
        path = _write_config(tmp_path, {"version": 1, "watch_targets": ["not-a-dict"]})
        with pytest.raises(ConfigError, match="object"):
            load_config(path)

    def test_missing_required_field_key(self, tmp_path):
        path = _write_config(tmp_path, {"version": 1, "watch_targets": [
            {"target_type": "github_repository", "name": "X", "locator": "https://x"}
        ]})
        with pytest.raises(ConfigError, match="key"):
            load_config(path)

    def test_target_with_invalid_priority_rejected(self, tmp_path):
        path = _write_config(tmp_path, {"version": 1, "watch_targets": [
            {
                "key": "x",
                "target_type": "github_repository",
                "name": "X",
                "locator": "https://x",
                "priority": 999,
            }
        ]})
        with pytest.raises(ConfigError, match="priority"):
            load_config(path)

    def test_target_with_unsupported_type_rejected(self, tmp_path):
        path = _write_config(tmp_path, {"version": 1, "watch_targets": [
            {
                "key": "x",
                "target_type": "twitter",
                "name": "X",
                "locator": "https://x",
            }
        ]})
        with pytest.raises(ConfigError, match="unsupported"):
            load_config(path)