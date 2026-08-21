from enum import StrEnum


class SignalType(StrEnum):
    CONTENT_CHANGE = "content_change"
    STARS_CHANGED = "stars_changed"
    RELEASE_PUBLISHED = "release_published"
    COMMIT_PUSHED = "commit_pushed"
    PR_STATUS_CHANGED = "pr_status_changed"
    ISSUE_UPDATED = "issue_updated"
