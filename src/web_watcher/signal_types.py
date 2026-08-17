from enum import StrEnum


class SignalType(StrEnum):
    CONTENT_CHANGE = "content_change"
    STARS_CHANGED = "stars_changed"
    RELEASE_PUBLISHED = "release_published"
