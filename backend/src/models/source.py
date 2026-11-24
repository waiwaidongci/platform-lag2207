from datetime import datetime
from sqlalchemy import JSON, Double, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
from src.constants.enums import Archive
from src.utils.time import now

class Dataset(Base):
    __tablename__ = 'datasets'
    __table_args__ = (UniqueConstraint('name', 'checksum'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    filename: Mapped[str] = mapped_column(String(160))
    checksum: Mapped[str] = mapped_column(String(64))
    rows: Mapped[list | None] = mapped_column(JSON)
    row_count: Mapped[int]
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)

class ArchiveReading(Base):
    __tablename__ = 'archive_readings'

    id: Mapped[int] = mapped_column(primary_key=True)
    signature: Mapped[str] = mapped_column(String(64), unique=True)
    platform: Mapped[str] = mapped_column(String(300))
    fan_id: Mapped[str] = mapped_column(String(300))
    timestamp: Mapped[str] = mapped_column(String(19))
    sound_dba: Mapped[float | None] = mapped_column(Double)
    fan_hz: Mapped[float | None] = mapped_column(Double)
    refs: Mapped[int] = mapped_column(default=0)
    size_bytes: Mapped[int] = mapped_column(default=0)

class DatasetArchive(Base):
    __tablename__ = 'dataset_archives'

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'), unique=True)
    state: Mapped[str] = mapped_column(String(20), default=Archive.BUILDING, index=True)
    resume_state: Mapped[str | None] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(String(500))
    row_count: Mapped[int] = mapped_column(default=0)
    original_bytes: Mapped[int] = mapped_column(default=0)
    map_bytes: Mapped[int] = mapped_column(default=0)
    reading_ids: Mapped[list | None] = mapped_column(JSON)
    mapped_count: Mapped[int] = mapped_column(default=0)
    reclaimed_count: Mapped[int] = mapped_column(default=0)
    unique_count: Mapped[int] = mapped_column(default=0)
    created_count: Mapped[int] = mapped_column(default=0)
    reclaimed_bytes: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)
    archived_at: Mapped[datetime | None]
    restored_at: Mapped[datetime | None]
