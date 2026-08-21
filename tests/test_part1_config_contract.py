"""Part 1 — Config Contract Ground Truth."""

import os
import tempfile

import pytest

from web_watcher.config import AppConfig, get_config
from web_watcher.scheduled_runner import ScheduledRunner


def test_default_cross_target_rules_path_is_none():
    config = get_config()
    assert getattr(config, "cross_target_rules_path", None) is None


def test_env_var_loading():
    os.environ["WEB_WATCHER_CROSS_TARGET_RULES_PATH"] = "/custom/path.yaml"
    try:
        config = get_config()
        assert config.cross_target_rules_path == "/custom/path.yaml"
    finally:
        del os.environ["WEB_WATCHER_CROSS_TARGET_RULES_PATH"]


def test_valid_yaml_loads():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(
            """version: "1.0"\n"""
            """cross_target_rules:\n"""
            """  - name: test\n"""
            """    entity_ids:\n"""
            """      - a\n"""
            """      - b\n"""
            """    window_seconds: 3600\n"""
            """    min_signals: 2\n"""
            """    importance_boost: high\n"""
        )
        f.flush()
        config = AppConfig(cross_target_rules_path=f.name)
        runner = ScheduledRunner(repo=None, config=config)
        assert runner.cross_target_correlator is not None
        assert len(runner.cross_target_correlator.rules) == 1


def test_invalid_yaml_structure_raises():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("- name: test\n  entity_ids: [a]\n")
        f.flush()
        config = AppConfig(cross_target_rules_path=f.name)
        with pytest.raises(ValueError):
            ScheduledRunner(repo=None, config=config)


def test_missing_cross_target_rules_section_raises():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write('version: "1.0"\n')
        f.flush()
        config = AppConfig(cross_target_rules_path=f.name)
        with pytest.raises(ValueError):
            ScheduledRunner(repo=None, config=config)


def test_missing_file_with_explicit_config_raises():
    config = AppConfig(cross_target_rules_path="/nonexistent/path.yaml")
    with pytest.raises(FileNotFoundError):
        ScheduledRunner(repo=None, config=config)


def test_reload_keeps_previous_rules_on_invalid_config():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as valid:
        valid.write(
            """version: "1.0"\n"""
            """cross_target_rules:\n"""
            """  - name: valid\n"""
            """    entity_ids:\n"""
            """      - a\n"""
            """      - b\n"""
            """    window_seconds: 3600\n"""
            """    min_signals: 2\n"""
            """    importance_boost: high\n"""
        )
        valid.flush()
        config = AppConfig(cross_target_rules_path=valid.name)
        runner = ScheduledRunner(repo=None, config=config)
        assert len(runner.cross_target_correlator.rules) == 1

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as invalid:
        invalid.write("invalid yaml: [\n")
        invalid.flush()
        # Reload with invalid file must not crash and must report the error.
        result = runner.reload_cross_target_rules(invalid.name)
        assert result["reloaded"] == 0
        assert "error" in result
        assert result["kept_previous_rules"] == 1
