"""旧釉料档案分段迁存：断点续迁、逐段核对、核对齐全后一次性切换。

- 每段的写入与核对在独立事务中提交，中断后续跑从已核对的段接着走。
- 段写入按（档案, 段号）幂等，重试不会产生重复段。
- 缺段或配方对不上时任务记为核对失败，档案仍读原件，原件保留。
- 全部段核对齐全后，在一个事务里切换档案为已迁存并清掉原件，
  不等待任何预检执行结束；切换前已提交的段保证读取始终拿到整份档案。
"""

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.config.settings import engine
from src.constants.workflow import Archive, Segment, Store
from src.models.tables import archive_jobs, archive_segments, audit, cases
from src.services import domain
from src.services.archive_plan import (assemble_payload, digest_of,
                                       needed_segments, plan_segments)
from src.utils.time import utc_time


def log(conn, actor, action, reference):
    conn.execute(audit.insert().values(actor=actor, action=action, reference=str(reference), created_at=utc_time()))


def found_case(conn, case_id):
    row = conn.execute(select(cases).where(cases.c.id == case_id)).mappings().first()
    if row is None:
        raise HTTPException(404, '找不到该记录')
    return dict(row)


def migrate(case_id, actor):
    """发起或续跑分段迁存；返回最新任务状态。"""
    with engine.begin() as conn:
        case = found_case(conn, case_id)
    if case['archive_state'] == Store.ARCHIVED:
        return status(case_id)
    payload = case['payload']
    if payload is None:
        raise HTTPException(409, '档案原件缺失且未完成迁存')
    try:
        domain.validate(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f'档案校验未通过，无法迁存：{exc}') from None
    manifest, planned = plan_segments(payload)
    _begin_job(case_id, actor, manifest)
    with engine.begin() as conn:
        stored = _segments(conn, case_id)
    try:
        for segment in needed_segments(planned, stored):
            if not _migrate_segment(case_id, segment):
                message = f"迁存段 {segment['index']} 核对不一致，原件保留"
                with engine.begin() as conn:
                    conn.execute(archive_jobs.update().where(archive_jobs.c.case_id == case_id)
                                 .values(status=Archive.FAILED, error=message, updated_at=utc_time()))
                    log(conn, actor, 'archive_failed', case_id)
                raise HTTPException(409, message)
    except IntegrityError:
        raise HTTPException(409, '另一个迁存任务正在写入，请稍后重试') from None
    return _switch(case_id, actor, manifest)


def _begin_job(case_id, actor, manifest):
    """登记或复位迁存任务；清单一致时保留已核对的段实现续跑。"""
    with engine.begin() as conn:
        job = conn.execute(select(archive_jobs).where(archive_jobs.c.case_id == case_id)).mappings().first()
        now = utc_time()
        if job and job['manifest'] == manifest:
            if job['status'] == Archive.FAILED:
                conn.execute(archive_jobs.update().where(archive_jobs.c.id == job['id'])
                             .values(status=Archive.MIGRATING, error=None, updated_at=now))
            return
        if job:
            conn.execute(archive_segments.delete().where(archive_segments.c.case_id == case_id))
            conn.execute(archive_jobs.update().where(archive_jobs.c.id == job['id'])
                         .values(status=Archive.MIGRATING, manifest=manifest,
                                 segment_count=len(manifest['segments']), verified_count=0,
                                 error=None, updated_at=now, switched_at=None))
        else:
            conn.execute(archive_jobs.insert().values(case_id=case_id, status=Archive.MIGRATING,
                         manifest=manifest, segment_count=len(manifest['segments']), verified_count=0,
                         operator=actor, created_at=now, updated_at=now))
        log(conn, actor, 'archive', case_id)


def _migrate_segment(case_id, segment):
    """写入并核对单个迁存段；已核对的段直接跳过。"""
    with engine.begin() as conn:
        stored = conn.execute(select(archive_segments)
                              .where(archive_segments.c.case_id == case_id,
                                     archive_segments.c.segment_index == segment['index'])).mappings().first()
        if (stored and stored['state'] == Segment.VERIFIED
                and stored['digest'] == segment['digest']
                and digest_of(stored['content']) == segment['digest']):
            return True
        now = utc_time()
        values = dict(first_seq=segment['first_seq'], last_seq=segment['last_seq'],
                      content=segment['rows'], digest=segment['digest'],
                      state=Segment.WRITTEN, verified_at=None)
        if stored:
            conn.execute(archive_segments.update()
                         .where(archive_segments.c.id == stored['id']).values(**values))
        else:
            conn.execute(archive_segments.insert().values(case_id=case_id,
                         segment_index=segment['index'], created_at=now, **values))
        reread = conn.execute(select(archive_segments)
                              .where(archive_segments.c.case_id == case_id,
                                     archive_segments.c.segment_index == segment['index'])).mappings().first()
        if not (reread and reread['digest'] == segment['digest']
                and digest_of(reread['content']) == segment['digest']):
            return False
        conn.execute(archive_segments.update().where(archive_segments.c.id == reread['id'])
                     .values(state=Segment.VERIFIED, verified_at=utc_time()))
        verified = conn.execute(select(func.count()).select_from(archive_segments)
                                .where(archive_segments.c.case_id == case_id,
                                       archive_segments.c.state == Segment.VERIFIED)).scalar_one()
        conn.execute(archive_jobs.update().where(archive_jobs.c.case_id == case_id)
                     .values(verified_count=verified, updated_at=utc_time()))
        return True


def _switch(case_id, actor, manifest):
    """核对齐全后在一个事务里切换并清掉原件；不齐全则保留原件并报错。"""
    with engine.begin() as conn:
        stored = _segments(conn, case_id)
    try:
        assemble_payload(manifest, stored)
    except ValueError as exc:
        with engine.begin() as conn:
            conn.execute(archive_jobs.update().where(archive_jobs.c.case_id == case_id)
                         .values(status=Archive.FAILED, error=str(exc), updated_at=utc_time()))
            log(conn, actor, 'archive_failed', case_id)
        raise HTTPException(409, f'迁存核对未通过，原件保留：{exc}') from None
    with engine.begin() as conn:
        count = conn.execute(cases.update()
                             .where(cases.c.id == case_id, cases.c.archive_state == Store.ORIGINAL)
                             .values(archive_state=Store.ARCHIVED, payload=None)).rowcount
        if count != 1:
            raise HTTPException(409, '档案状态已变化，请刷新后重试')
        now = utc_time()
        conn.execute(archive_jobs.update().where(archive_jobs.c.case_id == case_id)
                     .values(status=Archive.SWITCHED, error=None, updated_at=now, switched_at=now))
        log(conn, actor, 'archive_switch', case_id)
    return status(case_id)


def _segments(conn, case_id):
    return [dict(row) for row in conn.execute(select(archive_segments)
            .where(archive_segments.c.case_id == case_id)
            .order_by(archive_segments.c.segment_index)).mappings()]


def _job_with_segments(conn, job):
    segments = [public_segment(row) for row in _segments(conn, job['case_id'])]
    return {**job, 'segments': segments}


def public_segment(row):
    return {key: row[key] for key in
            ('segment_index', 'first_seq', 'last_seq', 'digest', 'state', 'verified_at')}


def status(case_id):
    with engine.connect() as conn:
        job = conn.execute(select(archive_jobs).where(archive_jobs.c.case_id == case_id)).mappings().first()
        if job is None:
            raise HTTPException(404, '该档案尚未发起迁存')
        return _job_with_segments(conn, dict(job))


def recent():
    with engine.connect() as conn:
        jobs = [dict(row) for row in conn.execute(select(archive_jobs)
                .order_by(archive_jobs.c.id.desc()).limit(100)).mappings()]
        return [_job_with_segments(conn, job) for job in jobs]


def effective_payload(case, conn):
    """读取档案当前有效内容：已迁存的档案从迁存段整份复原。

    缺段或配方对不上时回退原件；原件已清理且迁存段不完整时报错。
    """
    if case.get('archive_state') != Store.ARCHIVED:
        return case['payload']
    job = conn.execute(select(archive_jobs.c.manifest)
                       .where(archive_jobs.c.case_id == case['id'])).mappings().first()
    if job is not None:
        try:
            return assemble_payload(job['manifest'], _segments(conn, case['id']))
        except ValueError:
            pass
    if case.get('payload') is not None:
        return case['payload']
    raise HTTPException(409, '迁存档案缺段或配方不符，且原件已清理')
