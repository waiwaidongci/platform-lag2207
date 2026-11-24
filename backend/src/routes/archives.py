from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from src.config.database import connection
from src.constants.enums import Archive, Role
from src.controllers.auth import allow, current
from src.domain.analysis import missing_positions
from src.models.identity import User
from src.models.source import Dataset, DatasetArchive
from src.services import archiving
from src.services.audit import record
from src.services.datasets import checksum
from src.utils import views
from src.utils.wakeup import wake

router = APIRouter()


def require_archive(db, archive_id, lock=False):
    item = db.get(DatasetArchive, archive_id, with_for_update=lock)
    if not item:
        raise HTTPException(404, '找不到该归档记录')
    return item


@router.get('/archives')
def index(db: Session = Depends(connection), actor: User = Depends(current)):
    archives = {item.dataset_id: item for item in db.scalars(select(DatasetArchive))}
    items = [views.archive_item(dataset, archives.get(dataset.id))
             for dataset in db.scalars(select(Dataset).order_by(Dataset.id.desc()).limit(100))]
    return {'summary': archiving.summary(db), 'items': items}


@router.post('/datasets/{dataset_id}/archive', status_code=202)
def archive_dataset(dataset_id: int, db: Session = Depends(connection),
                    actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    dataset = db.get(Dataset, dataset_id)
    if not dataset:
        raise HTTPException(404, '找不到指定的数据批次')
    if dataset.rows is None:
        raise HTTPException(409, '该批次已归档，请先解档再重新归档')
    lookup = select(DatasetArchive).where(DatasetArchive.dataset_id == dataset.id)
    item = db.scalar(lookup.with_for_update())
    if item is None:
        try:
            with db.begin_nested():
                item = archiving.create(db, dataset, actor.username)
                record(db, actor.username, 'archive.queued', item.id, dataset_id=dataset.id)
        except IntegrityError:
            item = db.scalar(lookup.with_for_update())  # 并发请求已建档，幂等返回现状
    elif item.state == Archive.RETIRED:
        archiving.reset(item, dataset, actor.username)
        record(db, actor.username, 'archive.queued', item.id, dataset_id=dataset.id)
    elif item.state == Archive.FAILED:
        raise HTTPException(409, '归档处于失败状态，请先重试或放弃')
    elif item.state == Archive.ARCHIVED:
        raise HTTPException(409, '该批次已归档')
    # 进行中的归档幂等返回当前状态
    db.commit()
    wake()
    return views.archive(item)


@router.post('/archives/{archive_id}/restore', status_code=202)
def restore(archive_id: int, db: Session = Depends(connection),
            actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    item = require_archive(db, archive_id, lock=True)
    if item.state == Archive.ARCHIVED:
        item.state = Archive.RESTORING
        item.attempts = 0
        record(db, actor.username, 'archive.restore_queued', item.id, dataset_id=item.dataset_id)
    elif item.state in (Archive.RESTORING, Archive.RECLAIMING):
        pass  # 解档已在进行，幂等返回
    elif item.state == Archive.RETIRED:
        raise HTTPException(409, '该批次已复原，无需重复解档')
    else:
        raise HTTPException(409, '归档尚未完成，待释放原读数后才能解档')
    db.commit()
    wake()
    return views.archive(item)


@router.post('/archives/{archive_id}/retry', status_code=202)
def retry(archive_id: int, db: Session = Depends(connection),
          actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    item = require_archive(db, archive_id, lock=True)
    if item.state != Archive.FAILED:
        raise HTTPException(409, '只有失败的归档可以重试')
    item.state = item.resume_state or Archive.BUILDING
    item.resume_state = item.error = None
    item.attempts = 0
    record(db, actor.username, 'archive.retried', item.id, dataset_id=item.dataset_id)
    db.commit()
    wake()
    return views.archive(item)


@router.post('/archives/{archive_id}/abandon', status_code=202)
def abandon(archive_id: int, db: Session = Depends(connection),
            actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    item = require_archive(db, archive_id, lock=True)
    if item.state != Archive.FAILED:
        raise HTTPException(409, '只有失败的归档可以放弃')
    if item.resume_state not in archiving.ABANDONABLE:
        raise HTTPException(409, '复原阶段失败不能放弃，请重试')
    item.state = Archive.RECLAIMING  # 回收已引用的规范读数后退役，原批读数未动
    item.reclaimed_count = 0
    item.resume_state = item.error = None
    item.attempts = 0
    record(db, actor.username, 'archive.abandoned', item.id, dataset_id=item.dataset_id)
    db.commit()
    wake()
    return views.archive(item)


@router.get('/archives/{archive_id}/preview')
def preview(archive_id: int, offset: int = Query(0, ge=0), limit: int = Query(64, ge=1, le=500),
            sample_seconds: int = Query(10, ge=1, le=3600),
            db: Session = Depends(connection), actor: User = Depends(current)):
    item = require_archive(db, archive_id)
    if not item.reading_ids or item.mapped_count < item.row_count:
        raise HTTPException(409, '归档读数映射尚未完成，暂不能复原核对')
    dataset = db.get(Dataset, item.dataset_id)
    rows = archiving.reconstruct(db, item)
    if dataset is None or rows is None:
        raise HTTPException(409, '归档读数不完整，无法复原')
    return {'dataset_id': item.dataset_id, 'name': dataset.name, 'state': item.state,
            'consistent': checksum(rows) == dataset.checksum, 'checksum': dataset.checksum,
            'total': len(rows), 'rows': rows[offset:offset + limit],
            'sample_seconds': sample_seconds, 'missing': missing_positions(rows, sample_seconds)}
