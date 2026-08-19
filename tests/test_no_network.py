"""Phase 6 guardrails.

Contracts (fetch.py, adapters.py) must remain network-free.
Concrete adapters (github_repository_adapter.py, etc.) are allowed.
"""

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src" / "web_watcher"


# ---------------------------------------------------------------------------
# Contract files must stay pure — no network, no concrete adapters
# ---------------------------------------------------------------------------

CONTRACT_FILES = {"fetch.py", "adapters.py"}
CONTRACT_FORBIDDEN_IMPORTS = {
    "requests", "httpx", "urllib.request", "aiohttp", "http.client"
}


def test_contract_files_have_no_network_library_imports():
    for name in CONTRACT_FILES:
        source = (SRC_DIR / name).read_text(encoding="utf-8")
        assert not any(
            token in source
            for token in CONTRACT_FORBIDDEN_IMPORTS
        ), f"{name} must not import {CONTRACT_FORBIDDEN_IMPORTS}"


def test_adapters_module_is_pure_contract():
    source = (SRC_DIR / "adapters.py").read_text(encoding="utf-8")
    assert "class AdapterRegistry" in source
    assert "def resolve" in source
    assert "def fetch" not in source
    assert "http" not in source.lower()


def test_fetch_module_has_no_concrete_adapter_classes():
    source = (SRC_DIR / "fetch.py").read_text(encoding="utf-8")
    # Fetch.py should only have Protocol/ABC definitions, no concrete classes.
    # Enum subclasses are allowed because they are type definitions, not adapters.
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            # Allow Protocol and Enum subclasses
            base_names = {
                base.id for base in node.bases if isinstance(base, ast.Name)
            }
            if base_names and not (base_names <= {"Protocol", "Enum", "str"}):
                pytest.fail(
                    f"fetch.py defines concrete class {node.name} "
                    f"with bases {sorted(base_names)}"
                )


# ---------------------------------------------------------------------------
# Concrete adapters are allowed but must not define forbidden ones
# ---------------------------------------------------------------------------

FORBIDDEN_ADAPTER_NAMES = {
    "github.py", "web.py", "news.py",
    "github_adapter.py", "web_adapter.py", "news_adapter.py",
    "telegram_adapter.py", "ai_adapter.py", "browser_adapter.py",
}


def test_no_forbidden_concrete_adapters():
    for path in SRC_DIR.glob("**/*.py"):
        assert path.name not in FORBIDDEN_ADAPTER_NAMES, (
            f"forbidden adapter file: {path.name}"
        )


def test_all_python_files_compile():
    """Every .py under src must parse without error."""
    for path in SRC_DIR.rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"))
