"""Domain snapshots — immutable value objects for fetched source data."""

from dataclasses import dataclass


@dataclass(frozen=True)
class GitHubRepositorySnapshot:
    """Immutable snapshot of a GitHub repository at a point in time."""

    name: str
    full_name: str
    description: str | None
    html_url: str
    stars: int
    forks: int
    open_issues: int
    default_branch: str | None
    created_at: str | None
    updated_at: str | None
    pushed_at: str | None
    license_spdx_id: str | None
    archived: bool
    visibility: str | None
