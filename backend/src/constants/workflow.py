from enum import StrEnum


class Role(StrEnum):
    OPERATOR = 'operator'
    REVIEWER = 'reviewer'
    VIEWER = 'viewer'


class Status(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETE = 'complete'
    FAILED = 'failed'


class Store(StrEnum):
    ORIGINAL = 'original'
    ARCHIVED = 'archived'


class Archive(StrEnum):
    MIGRATING = 'migrating'
    SWITCHED = 'switched'
    FAILED = 'failed'


class Segment(StrEnum):
    WRITTEN = 'written'
    VERIFIED = 'verified'
