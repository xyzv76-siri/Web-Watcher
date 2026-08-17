from enum import StrEnum


class EventStatus(StrEnum):
    NEW = "new"
    PROCESSED = "processed"
    DISCARDED = "discarded"
