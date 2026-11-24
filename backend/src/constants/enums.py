from enum import StrEnum

class Role(StrEnum):
    ADMIN = 'admin'
    ANALYST = 'analyst'
    VIEWER = 'viewer'

class Status(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETE = 'complete'
    FAILED = 'failed'

class Review(StrEnum):
    PENDING = 'pending'
    ACCEPTED = 'accepted'
    RETURNED = 'returned'

class Archive(StrEnum):
    BUILDING = 'building'
    VERIFYING = 'verifying'
    RELEASING = 'releasing'
    ARCHIVED = 'archived'
    RESTORING = 'restoring'
    RECLAIMING = 'reclaiming'
    RETIRED = 'retired'
    FAILED = 'failed'
