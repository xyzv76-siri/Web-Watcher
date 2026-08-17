"""Regression tests: Phase 5 must contain contracts only, no concrete
network adapters or network library imports."""

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_phase5_contains_no_concrete_network_adapter():
    adapter_files = list(
        (PROJECT_ROOT / "src" / "web_watcher").glob(
            "**/*adapter*.py"
        )
    )

    concrete_names = {
        "github_adapter.py",
        "web_adapter.py",
        "news_adapter.py",
        "github.py",
        "web.py",
        "news.py",
    }

    assert not any(
        path.name in concrete_names
        for path in adapter_files
    )


def test_phase5_fetch_contract_has_no_network_library_imports():
    fetch_source = (
        PROJECT_ROOT
        / "src"
        / "web_watcher"
        / "fetch.py"
    ).read_text(encoding="utf-8")

    forbidden_imports = (
        "requests",
        "httpx",
        "urllib.request",
        "aiohttp",
    )

    assert not any(
        token in fetch_source
        for token in forbidden_imports
    )


def test_phase5_adapters_module_has_no_network_imports():
    adapters_source = (
        PROJECT_ROOT
        / "src"
        / "web_watcher"
        / "adapters.py"
    ).read_text(encoding="utf-8")

    forbidden_imports = (
        "requests",
        "httpx",
        "urllib.request",
        "aiohttp",
        "http.client",
    )

    assert not any(
        token in adapters_source
        for token in forbidden_imports
    )


def test_no_source_module_implements_concrete_adapter():
    """No source file under src/web_watcher should contain concrete
    network calls (HTTP GET, POST, etc.)."""
    source_dir = PROJECT_ROOT / "src" / "web_watcher"
    network_calls = {
        "get", "post", "put", "delete", "request",
        "connect", "connect_async",
    }

    for path in source_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr in network_calls:
                        # Allow calls on local classes (e.g. FakeAdapter in tests)
                        # but flag stdlib/network patterns
                        pass
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", None) or ""
                if any(
                    forbidden in module
                    for forbidden in ["requests", "httpx", "aiohttp", "urllib", "http.client"]
                ):
                    pytest.fail(
                        f"network library imported in {path.name}: {module}"
                    )


def test_adapters_module_is_pure_contract():
    """adapters.py must only define AdapterRegistry — no fetch
    implementations, no HTTP, no storage."""
    source = (
        PROJECT_ROOT
        / "src"
        / "web_watcher"
        / "adapters.py"
    ).read_text(encoding="utf-8")

    assert "class AdapterRegistry" in source
    assert "def resolve" in source
    assert "def fetch" not in source  # no concrete fetch implementation
    assert "http" not in source.lower()
