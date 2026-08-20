"""Static validation tests for Docker / production packaging artifacts."""

from __future__ import annotations

import os
import re
import stat
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_has_production_dependencies() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)

    deps = data.get("project", {}).get("dependencies", [])
    names = {dep.split(">=")[0].split("==")[0].lower() for dep in deps}
    assert "requests" in names, "pyproject.toml must declare requests"
    assert "pyyaml" in names, "pyproject.toml must declare pyyaml"


def test_pyproject_has_entry_point() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)

    scripts = data.get("project", {}).get("scripts", {})
    assert "web-watcher" in scripts, "pyproject.toml must define web-watcher console script"


def test_dockerfile_exists() -> None:
    assert (PROJECT_ROOT / "Dockerfile").is_file()


def test_dockerfile_non_root_user() -> None:
    content = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^USER\s+\S+", content, re.MULTILINE), "Dockerfile must switch to non-root USER"


def test_dockerfile_production_deps_only() -> None:
    content = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    # Must not install dev/test extras
    assert "pytest" not in content, "Dockerfile must not install pytest"
    assert ".dev" not in content and "dev" not in content.split("pip install"), (
        "Dockerfile must not install dev extras"
    )


def test_dockerfile_no_local_database() -> None:
    content = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "web_watcher.db" not in content, "Dockerfile must not bake local database into image"
    assert "COPY *.db" not in content, "Dockerfile must not copy database files"


def test_dockerfile_python_runtime_matches_pyproject() -> None:
    content = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)
    requires = data.get("project", {}).get("requires-python", "")
    major_minor = ".".join(requires.strip(">=~").split(".")[:2])
    assert f"python:{major_minor}" in content or f"python:{requires}" in content, (
        f"Dockerfile base image should declare Python {requires}"
    )


def test_dockerignore_exists() -> None:
    assert (PROJECT_ROOT / ".dockerignore").is_file()


def test_dockerignore_excludes_secrets() -> None:
    content = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".env" in content, ".dockerignore must exclude .env"
    assert "secrets" in content, ".dockerignore must exclude secrets directory"


def test_dockerignore_excludes_databases() -> None:
    content = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "*.db" in content, ".dockerignore must exclude local databases"


def test_dockerignore_does_not_exclude_production_config() -> None:
    content = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
    # config/ is not excluded
    assert "config/" not in content.splitlines(), ".dockerignore must not exclude config/"


def test_docker_compose_exists() -> None:
    assert (PROJECT_ROOT / "docker-compose.yml").is_file()


def test_docker_compose_has_volumes() -> None:
    content = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "volumes:" in content, "docker-compose.yml must declare named volumes"
    assert "web-watcher-data" in content, "docker-compose.yml must have data volume"


def test_docker_compose_has_restart_policy() -> None:
    content = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "restart:" in content, "docker-compose.yml must declare restart policy"


def test_entrypoint_exists_and_executable() -> None:
    path = PROJECT_ROOT / "entrypoint.sh"
    assert path.is_file()
    mode = os.stat(path).st_mode
    assert mode & stat.S_IXUSR, "entrypoint.sh must be executable"


def test_entrypoint_creates_runtime_dirs() -> None:
    content = (PROJECT_ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    assert "/data" in content, "entrypoint.sh must ensure /data exists"
    assert "/logs" in content, "entrypoint.sh must ensure /logs exists"


def test_docker_run_has_signal_handling() -> None:
    content = (PROJECT_ROOT / "src" / "web_watcher" / "docker_run.py").read_text(encoding="utf-8")
    assert "SIGTERM" in content, "docker_run.py must handle SIGTERM"
    assert "signal.signal" in content, "docker_run.py must register signal handler"


def test_docker_run_validates_config() -> None:
    content = (PROJECT_ROOT / "src" / "web_watcher" / "docker_run.py").read_text(encoding="utf-8")
    assert "_validate_config" in content, "docker_run.py must validate configuration"
    assert "_validate_database" in content, "docker_run.py must validate database"


def test_docker_run_uses_scheduled_runner() -> None:
    content = (PROJECT_ROOT / "src" / "web_watcher" / "docker_run.py").read_text(encoding="utf-8")
    assert "ScheduledRunner" in content, "docker_run.py must use ScheduledRunner"
    assert "run_once" in content, "docker_run.py must call run_once in a loop"


def test_no_secrets_in_repo() -> None:
    """Quick sanity check that no obvious live credentials are checked in."""
    patterns = [
        re.compile(r"ghp_[A-Za-z0-9]{20,}"),
        re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
        re.compile(r"xox[baprs]-[A-Za-z0-9-]+"),
        re.compile(r"sk_live_[A-Za-z0-9]+"),
        re.compile(r"AKIA[0-9A-Z]{16}"),
    ]
    text = ""
    for root, _dirs, files in os.walk(PROJECT_ROOT):
        if ".git" in root:
            continue
        for name in files:
            if name.endswith(".pyc"):
                continue
            path = Path(root) / name
            try:
                text += path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
    hits = [p for p in patterns if p.search(text)]
    assert not hits, f"Possible secrets found in repo: {[p.pattern for p in hits]}"
