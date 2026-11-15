"""名册导入服务。

覆盖需求：
- 按来源记录标识 source_ref + 内容指纹 content_fingerprint 识别 重复 / 更正 / 撤回。
- 行级处理与断点续导：部分行失败时文档为 partial_failed，可从首个未处理行继续，整份名册不算成功。
- 更正名册到达后，引用旧名册的待发布分析运行立即过期（stale），历史报告保留原来源快照。
"""
import hashlib
import json
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models
from ..schemas import RosterImportIn
from .revisions import create_revision
from .subjects import resolve_or_create_holder

UNIDENTIFIED_TOKENS = ("未知持有人", "未知", "待确认持有人", "unidentified")


def content_fingerprint(payload: RosterImportIn) -> str:
    """对名册内容做规范序列化后取 SHA-256；同一来源重复推送得到相同指纹。"""
    canonical = json.dumps(
        {
            "company_name": payload.company_name,
            "company_unified_code": payload.company_unified_code,
            "effective_from": str(payload.effective_from),
            "effective_to": str(payload.effective_to) if payload.effective_to else None,
            "rows": [r.model_dump() for r in payload.rows],
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _mark_runs_stale(db: AsyncSession, old_doc_id: int, reason: str) -> int:
    """引用旧文档的待发布(draft)运行标记过期；已发布历史报告保持原样。"""
    run_ids = await db.scalars(
        select(models.AnalysisRunDoc.run_id).where(
            models.AnalysisRunDoc.document_id == old_doc_id
        )
    )
    count = 0
    for run_id in run_ids:
        run = await db.get(models.AnalysisRun, run_id)
        if run and run.status == models.RUN_DRAFT:
            run.status = models.RUN_STALE
            run.failure_reason = reason
            count += 1
    return count


def _validate_row(row) -> str | None:
    if not row.share_class:
        return "股本类别为空"
    if row.shares < 0:
        return "股份数为负"
    if row.is_total and (row.registered_total is None or row.registered_total <= 0):
        return "总量行缺少登记总量"
    if row.effective_to and row.effective_to < row.effective_from:
        return "有效区间结束早于开始"
    if row.votes_per_share < 0:
        return "每股表决权为负"
    if not row.is_total and not row.holder_raw_name:
        return "持有行缺少持有人名称"
    return None


async def _process_pending_rows(db: AsyncSession, doc: models.RosterDocument) -> dict:
    """从 doc.next_row_seq 开始处理未处理行，返回统计与失败明细。"""
    succeeded = failed = 0
    failures: list[dict] = []
    rows = list(await db.scalars(
        select(models.RosterRow)
        .where(models.RosterRow.document_id == doc.id)
        .order_by(models.RosterRow.row_seq)
    ))
    for row in rows:
        if row.process_status != models.ROW_PENDING:
            continue
        doc.next_row_seq = row.row_seq
        try:
            err = _validate_row(row)
            if err:
                raise ValueError(err)
            if not row.is_total:
                raw = (row.holder_raw_name or "").strip()
                if raw in UNIDENTIFIED_TOKENS or (not row.holder_subject_id and not raw):
                    # 未知持有人：不建主体，保留独立份额
                    row.holder_subject_id = None
                elif row.holder_subject_id is None and raw:
                    # 无统一标识的行延迟到此处解析，保证单行失败后可从断点续导
                    subject = await resolve_or_create_holder(
                        db, name=raw, code=None, subject_type=row.holder_type
                    )
                    row.holder_subject_id = subject.id
            row.process_status = models.ROW_OK
            succeeded += 1
        except Exception as exc:  # 行级失败不影响其他行
            row.process_status = models.ROW_FAILED
            row.error_detail = str(exc)
            failed += 1
            failures.append({"row_seq": row.row_seq, "error": str(exc)})
        await db.flush()

    doc.next_row_seq = doc.total_rows
    if failed == 0:
        doc.process_status = models.DOC_IMPORTED
    else:
        doc.process_status = models.DOC_PARTIAL
    return {"succeeded": succeeded, "failed": failed, "failures": failures}


async def import_roster(db: AsyncSession, payload: RosterImportIn, user: str) -> dict:
    fp = content_fingerprint(payload)

    # 1) 撤回
    if payload.withdrawal:
        old_docs = list(await db.scalars(
            select(models.RosterDocument).where(
                models.RosterDocument.source_ref == payload.source_ref
            )
        ))
        if not old_docs:
            return _result("withdrawal", None, None, 0, 0, 0, 0, False,
                           "来源标识无对应名册，忽略撤回", [])
        stale_count = 0
        for old in old_docs:
            old.process_status = models.DOC_WITHDRAWN
            rev = await db.get(models.Revision, old.revision_id)
            if rev:
                rev.status = models.ST_WITHDRAWN
            stale_count += await _mark_runs_stale(db, old.id, "来源名册已撤回，结果过期")
        db.add(models.ImportEvent(
            source_ref=payload.source_ref, content_fingerprint=fp,
            event_kind="withdrawal", message=f"撤回 {len(old_docs)} 份，过期运行 {stale_count} 个",
        ))
        await db.flush()
        return _result("withdrawal", old_docs[-1].id, models.DOC_WITHDRAWN, 0, 0, 0, 0,
                       False, f"已撤回并使 {stale_count} 个待发布结果过期", [])

    # 2) 重复：同来源 + 同指纹直接命中，不产生新数据
    dup = await db.scalar(
        select(models.RosterDocument).where(
            models.RosterDocument.source_ref == payload.source_ref,
            models.RosterDocument.content_fingerprint == fp,
        )
    )
    if dup:
        db.add(models.ImportEvent(
            source_ref=payload.source_ref, content_fingerprint=fp,
            event_kind="duplicate", document_id=dup.id, message="内容指纹一致，判定重复导入",
        ))
        await db.flush()
        return _result("duplicate", dup.id, dup.process_status, dup.total_rows, 0, 0,
                       dup.next_row_seq, True, "重复名册，已忽略", [])

    # 3) 更正或新建：先解析公司主体
    company = await resolve_or_create_holder(
        db, payload.company_name, payload.company_unified_code, models.SUBJ_COMPANY, user
    )

    doc = models.RosterDocument(
        source_ref=payload.source_ref,
        content_fingerprint=fp,
        company_subject_id=company.id,
        doc_title=payload.doc_title,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        process_status=models.DOC_PROCESSING,
        total_rows=len(payload.rows),
        next_row_seq=0,
    )
    db.add(doc)
    await db.flush()
    rev = await create_revision(
        db, models.REV_ROSTER_DOC, doc.id,
        f"导入名册 {payload.doc_title}（来源 {payload.source_ref}）", user,
        source_doc_id=doc.id,
    )
    doc.revision_id = rev.id

    # 持有行先做带代码的主体解析（无代码的延迟到行处理，保证失败可续导）
    for seq, r in enumerate(payload.rows):
        holder_id = None
        raw_name = r.holder_name
        if not r.is_total and r.holder_name and r.holder_name not in UNIDENTIFIED_TOKENS:
            if r.holder_unified_code:
                holder = await resolve_or_create_holder(
                    db, r.holder_name, r.holder_unified_code, r.holder_type, user
                )
                holder_id = holder.id
        row = models.RosterRow(
            document_id=doc.id,
            row_seq=seq,
            holder_subject_id=holder_id,
            holder_raw_name=None if r.is_total else (r.holder_name or "未知持有人"),
            holder_type=r.holder_type,
            share_class=r.share_class,
            shares=r.shares,
            votes_per_share=r.votes_per_share,
            is_total=r.is_total,
            registered_total=r.registered_total,
            effective_from=r.effective_from,
            effective_to=r.effective_to,
            provenance=r.provenance,
            process_status=models.ROW_PENDING,
            revision_id=rev.id,
        )
        db.add(row)
    await db.flush()

    stats = await _process_pending_rows(db, doc)

    # 4) 更正关系：旧来源名册置 superseded，待发布运行过期
    event_kind = "new"
    message = "新名册导入完成"
    stale_count = 0
    if payload.corrects_source_ref:
        old_docs = list(await db.scalars(
            select(models.RosterDocument).where(
                models.RosterDocument.source_ref == payload.corrects_source_ref
            )
        ))
        for old in old_docs:
            old.corrected_by_doc_id = doc.id
            old_rev = await db.get(models.Revision, old.revision_id)
            if old_rev and old_rev.status == models.ST_APPROVED:
                old_rev.status = models.ST_SUPERSEDED
            stale_count += await _mark_runs_stale(
                db, old.id, f"更正名册 {payload.source_ref} 到达，待发布结果过期"
            )
        event_kind = "correction"
        message = f"更正名册导入完成，旧名册 {len(old_docs)} 份置为 superseded，{stale_count} 个待发布结果过期"

    db.add(models.ImportEvent(
        source_ref=payload.source_ref, content_fingerprint=fp, event_kind=event_kind,
        document_id=doc.id, succeeded_rows=stats["succeeded"], failed_rows=stats["failed"],
        message=message,
    ))
    await db.flush()
    return _result(
        event_kind, doc.id, doc.process_status, doc.total_rows,
        stats["succeeded"], stats["failed"], doc.next_row_seq, False, message, stats["failures"]
    )


async def repair_failed_row(db: AsyncSession, row_id: int, shares, votes_per_share,
                            holder_name: str | None, user: str) -> dict:
    """人工修正失败行后将其重置为 pending，供 resume 从断点续导。"""
    row = await db.get(models.RosterRow, row_id)
    if not row:
        return {"ok": False, "reason": "名册行不存在"}
    if row.process_status != models.ROW_FAILED:
        return {"ok": False, "reason": f"行状态为 {row.process_status}，仅失败行可修正"}
    row.shares = shares
    if votes_per_share is not None:
        row.votes_per_share = votes_per_share
    if holder_name is not None:
        row.holder_raw_name = holder_name
    row.error_detail = None
    row.process_status = models.ROW_PENDING
    doc = await db.get(models.RosterDocument, row.document_id)
    if doc:
        # 仍存在失败/未处理行，文档保持 partial_failed，等待 resume 续导
        doc.process_status = models.DOC_PARTIAL
        # 续导指针回到最早的未处理行
        first_pending = await db.scalar(
            select(func.min(models.RosterRow.row_seq)).where(
                models.RosterRow.document_id == row.document_id,
                models.RosterRow.process_status == models.ROW_PENDING,
            )
        )
        doc.next_row_seq = first_pending if first_pending is not None else doc.total_rows
    await db.flush()
    return {"ok": True, "document_id": row.document_id}


async def resume_import(db: AsyncSession, source_ref: str) -> dict:
    """对 partial_failed 的名册从首个未处理行续导。"""
    doc = await db.scalar(
        select(models.RosterDocument).where(
            models.RosterDocument.source_ref == source_ref,
            models.RosterDocument.process_status == models.DOC_PARTIAL,
        )
    )
    if not doc:
        return _result("resume", None, None, 0, 0, 0, 0, False,
                       "没有处于 partial_failed 的该来源名册", [])
    stats = await _process_pending_rows(db, doc)
    db.add(models.ImportEvent(
        source_ref=source_ref, content_fingerprint=doc.content_fingerprint,
        event_kind="resume", document_id=doc.id, row_offset=doc.next_row_seq,
        succeeded_rows=stats["succeeded"], failed_rows=stats["failed"],
        message=f"续导：成功 {stats['succeeded']} 行，失败 {stats['failed']} 行",
    ))
    await db.flush()
    return _result("resume", doc.id, doc.process_status, doc.total_rows,
                   stats["succeeded"], stats["failed"], doc.next_row_seq, False,
                   "续导完成", stats["failures"])


def _result(kind, doc_id, status, total, ok, failed, next_seq, duplicate, message, failures):
    return {
        "event_kind": kind,
        "document_id": doc_id,
        "process_status": status,
        "total_rows": total,
        "succeeded_rows": ok,
        "failed_rows": failed,
        "next_row_seq": next_seq,
        "duplicate": duplicate,
        "message": message,
        "failures": failures,
    }
