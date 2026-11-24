"""历史批次的读数去重归档、复原核对与空间回收。

同站台同风机同一时刻的相同读数在归档库只存一份（冲突值各自保留），原行序
以读数编号映射照留。状态机：building → verifying → releasing → archived；
解档走 restoring → reclaiming → retired。worker 每轮推进一步并落库进度，
中断后按进度续做；占用量由状态与现存读数实时汇总，重试不会重复扣减。
"""

import hashlib
import json
import logging
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from src.config.database import Session
from src.constants.enums import Archive, Status
from src.models.jobs import Run
from src.models.source import ArchiveReading, Dataset, DatasetArchive
from src.services.audit import record
from src.services.datasets import checksum
from src.utils.time import now

log = logging.getLogger(__name__)

CHUNK = 500  # 每步处理的行数，控制单事务大小，中断后从进度续做
KEYS = ('platform', 'fan_id', 'timestamp', 'sound_dba', 'fan_hz')
ACTIVE = (Archive.BUILDING, Archive.VERIFYING, Archive.RELEASING, Archive.RESTORING, Archive.RECLAIMING)
ABANDONABLE = (Archive.BUILDING, Archive.VERIFYING, Archive.RELEASING)


def json_size(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return len(raw.encode('utf-8'))


def column_size(value):
    """JSON 列实际落库的字节估计，与 SQLAlchemy 序列化口径一致。"""
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))


def canonical(row):
    payload = {key: row.get(key) for key in KEYS}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest(), payload, len(raw.encode('utf-8'))


def create(db, dataset, actor):
    item = DatasetArchive(dataset_id=dataset.id, state=Archive.BUILDING, row_count=dataset.row_count,
                          original_bytes=column_size(dataset.rows), reading_ids=[], author=actor)
    db.add(item)
    db.flush()
    return item


def reset(item, dataset, actor):
    """已退役的归档重新建档；此时不持有任何读数引用。"""
    item.state = Archive.BUILDING
    item.resume_state = item.error = None
    item.row_count = dataset.row_count
    item.original_bytes = column_size(dataset.rows)
    item.reading_ids = []
    item.map_bytes = item.mapped_count = item.reclaimed_count = 0
    item.unique_count = item.created_count = item.reclaimed_bytes = item.attempts = 0
    item.archived_at = item.restored_at = None
    item.author = actor
    return item


def summary(db):
    """净省空间 = 已释放原批字节 − 归档库当前占用，按现存状态实时汇总。"""
    released = db.scalar(select(func.coalesce(func.sum(DatasetArchive.original_bytes), 0))
                         .where(DatasetArchive.state == Archive.ARCHIVED))
    readings = db.scalar(select(func.coalesce(func.sum(ArchiveReading.size_bytes), 0)))
    maps = db.scalar(select(func.coalesce(func.sum(DatasetArchive.map_bytes), 0))
                     .where(DatasetArchive.reading_ids.is_not(None)))
    counts = dict(db.execute(select(DatasetArchive.state, func.count())
                             .group_by(DatasetArchive.state)).all())
    store = readings + maps
    return {'released_bytes': released, 'readings_bytes': readings, 'map_bytes': maps,
            'store_bytes': store, 'net_saved': released - store,
            'archived_count': counts.get(Archive.ARCHIVED, 0),
            'active_count': sum(counts.get(state, 0) for state in ACTIVE),
            'retired_count': counts.get(Archive.RETIRED, 0)}


def reconstruct(db, item):
    """按行序映射复原原批读数；读数缺失时返回 None。"""
    ids = item.reading_ids or []
    if not ids:
        return None
    found = {reading.id: reading for reading in
             db.scalars(select(ArchiveReading).where(ArchiveReading.id.in_(ids)))}
    if len(found) < len(set(ids)):
        return None
    return [{key: getattr(found[reading_id], key) for key in KEYS} for reading_id in ids]


def _ref_reading(db, signature, payload, size):
    """取或建规范读数并引用计数 +1；并发归档引用同一行时彼此留住。"""
    created = False
    for _attempt in range(4):
        reading = db.scalar(select(ArchiveReading).where(ArchiveReading.signature == signature))
        if reading is None:
            try:
                with db.begin_nested():
                    reading = ArchiveReading(signature=signature, refs=0, size_bytes=size, **payload)
                    db.add(reading)
                    db.flush()
                created = True
            except IntegrityError:
                continue  # 并发归档已建档同一读数，重新读取
        updated = db.execute(update(ArchiveReading).where(ArchiveReading.id == reading.id)
                             .values(refs=ArchiveReading.refs + 1)).rowcount
        if updated:
            return reading.id, created
        # 该行正被并发回收删除，下一轮重新取或建
    raise RuntimeError('归档读数引用冲突，请重试')


def _fail(db, item, message):
    item.resume_state = item.state
    item.state = Archive.FAILED
    item.error = message[:500]
    record(db, 'worker', 'archive.failed', item.id, error=item.error)


def _build_chunk(db, item):
    dataset = db.get(Dataset, item.dataset_id)
    rows = dataset.rows if dataset else None
    if not rows:
        _fail(db, item, '原批读数不可用，无法继续归档')
        return True
    ids = list(item.reading_ids or [])
    chunk = rows[item.mapped_count:item.mapped_count + CHUNK]
    created = 0
    for row in chunk:
        signature, payload, size = canonical(row)
        reading_id, is_new = _ref_reading(db, signature, payload, size)
        created += is_new
        ids.append(reading_id)
    item.reading_ids = ids
    item.mapped_count += len(chunk)
    item.created_count += created
    item.map_bytes = column_size(ids)
    if item.mapped_count >= len(rows):
        item.row_count = len(rows)
        item.unique_count = len(set(ids))
        item.state = Archive.VERIFYING
        record(db, 'worker', 'archive.built', item.id, dataset_id=item.dataset_id,
               rows=item.row_count, unique=item.unique_count, created=item.created_count)
    return True


def _verify(db, item):
    dataset = db.get(Dataset, item.dataset_id)
    rows = reconstruct(db, item)
    if dataset is None or rows is None or checksum(rows) != dataset.checksum:
        _fail(db, item, '复原核对与原批指纹不一致，已保留原批读数')
        return True
    item.state = Archive.RELEASING
    record(db, 'worker', 'archive.verified', item.id, dataset_id=item.dataset_id)
    return _release(db, item)


def _release(db, item):
    dataset = db.get(Dataset, item.dataset_id, with_for_update=True, populate_existing=True)
    inflight = db.scalar(select(func.count()).select_from(Run)
                         .where(Run.dataset_id == item.dataset_id,
                                Run.status.in_((Status.QUEUED, Status.RUNNING))))
    if inflight:
        return False  # 在途扫描照常取原批，下轮再释放
    dataset.rows = None
    item.state = Archive.ARCHIVED
    item.archived_at = now()
    record(db, 'worker', 'archive.released', item.id, dataset_id=item.dataset_id,
           freed_bytes=item.original_bytes)
    return True


def _restore(db, item):
    dataset = db.get(Dataset, item.dataset_id, with_for_update=True, populate_existing=True)
    rows = reconstruct(db, item)
    if dataset is None or rows is None or checksum(rows) != dataset.checksum:
        _fail(db, item, '复原核对不一致，请重试或检查归档读数')
        return True
    dataset.rows = rows
    item.state = Archive.RECLAIMING
    item.restored_at = now()
    record(db, 'worker', 'archive.restored', item.id, dataset_id=item.dataset_id, rows=len(rows))
    return True


def _reclaim_chunk(db, item):
    ids = item.reading_ids or []
    chunk = ids[item.reclaimed_count:item.reclaimed_count + CHUNK]
    freed = 0
    for reading_id in chunk:
        db.execute(update(ArchiveReading).where(ArchiveReading.id == reading_id)
                   .values(refs=ArchiveReading.refs - 1))
        reading = db.get(ArchiveReading, reading_id)
        if reading is None or reading.refs > 0:
            continue  # 并发归档仍引用同一行，留住
        removed = db.execute(delete(ArchiveReading)
                             .where(ArchiveReading.id == reading_id, ArchiveReading.refs <= 0)).rowcount
        if removed:
            freed += reading.size_bytes
    item.reclaimed_bytes += freed
    item.reclaimed_count += len(chunk)
    if item.reclaimed_count >= len(ids):
        item.reading_ids = None
        item.map_bytes = item.mapped_count = item.reclaimed_count = 0
        item.state = Archive.RETIRED
        record(db, 'worker', 'archive.reclaimed', item.id, dataset_id=item.dataset_id,
               reclaimed_bytes=item.reclaimed_bytes)
    return True


def _step(db, item):
    if item.state == Archive.BUILDING:
        return _build_chunk(db, item)
    if item.state == Archive.VERIFYING:
        return _verify(db, item)
    if item.state == Archive.RELEASING:
        return _release(db, item)
    if item.state == Archive.RESTORING:
        return _restore(db, item)
    if item.state == Archive.RECLAIMING:
        return _reclaim_chunk(db, item)
    return False


def _advance_one(archive_id):
    try:
        with Session.begin() as db:
            item = db.get(DatasetArchive, archive_id, with_for_update=True)
            if item is None or item.state not in ACTIVE:
                return False
            progressed = _step(db, item)
            if progressed:
                item.attempts = 0
            return progressed
    except Exception:
        log.exception('Archive %s step failed', archive_id)
        with Session.begin() as db:
            item = db.get(DatasetArchive, archive_id, with_for_update=True)
            if item is None or item.state not in ACTIVE:
                return True
            item.attempts += 1
            item.error = '后台步骤中断，将自动重试'
            if item.attempts >= 5:
                _fail(db, item, '归档步骤多次中断，请人工重试')
        return True


def advance():
    """推进一步可做的归档/解档；返回是否有进展，供 worker 决定是否继续。"""
    with Session.begin() as db:
        ids = db.scalars(select(DatasetArchive.id).where(DatasetArchive.state.in_(ACTIVE))
                         .order_by(DatasetArchive.id).limit(5)
                         .with_for_update(skip_locked=True)).all()
    for archive_id in ids:
        if _advance_one(archive_id):
            return True
    return False
