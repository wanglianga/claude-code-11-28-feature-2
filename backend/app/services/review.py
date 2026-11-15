"""修订审核与股本类别总量校验。"""
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models


async def list_pending(db: AsyncSession) -> list[models.Revision]:
    result = await db.scalars(
        select(models.Revision)
        .where(models.Revision.status.in_([models.ST_DRAFT, models.ST_REVIEW]))
        .order_by(models.Revision.id)
    )
    return list(result)


async def check_class_totals(
    db: AsyncSession, doc: models.RosterDocument, tolerance: Decimal
) -> list[dict]:
    """同一类别持有行股份合计必须等于该类别登记总量。返回不通过项（空列表表示通过）。"""
    rows = list(await db.scalars(
        select(models.RosterRow).where(
            models.RosterRow.document_id == doc.id,
            models.RosterRow.process_status == models.ROW_OK,
        )
    ))
    totals: dict[str, Decimal] = {}
    sums: dict[str, Decimal] = {}
    for row in rows:
        if row.is_total and row.registered_total is not None:
            totals[row.share_class] = row.registered_total
        elif not row.is_total:
            sums[row.share_class] = sums.get(row.share_class, Decimal("0")) + row.shares

    problems: list[dict] = []
    for cls, registered in totals.items():
        actual = sums.get(cls, Decimal("0"))
        if abs(actual - registered) > tolerance:
            problems.append({
                "share_class": cls,
                "registered_total": str(registered),
                "holder_sum": str(actual),
                "difference": str(actual - registered),
            })
    # 持有行出现了但没有登记总量行的类别也不能通过
    for cls, actual in sums.items():
        if cls not in totals:
            problems.append({
                "share_class": cls,
                "registered_total": None,
                "holder_sum": str(actual),
                "difference": None,
            })
    return problems


async def review_document(
    db: AsyncSession, revision: models.Revision, approve: bool, user: str,
    note: str | None, tolerance: Decimal,
) -> dict:
    doc = await db.get(models.RosterDocument, revision.entity_id)
    if not doc:
        return {"ok": False, "reason": "修订对应名册不存在"}
    if revision.status not in (models.ST_DRAFT, models.ST_REVIEW):
        return {"ok": False, "reason": f"修订状态为 {revision.status}，不可审核"}

    if not approve:
        revision.status = models.ST_REJECTED
        revision.reviewed_by = user
        revision.change_note = f"{revision.change_note} | 驳回：{note or ''}"
        doc.process_status = models.DOC_REJECTED
        await db.flush()
        return {"ok": True, "approved": False}

    if doc.process_status == models.DOC_PARTIAL:
        return {"ok": False, "reason": "名册存在失败行，须先续导成功才能审核"}

    problems = await check_class_totals(db, doc, tolerance)
    if problems:
        return {
            "ok": False,
            "reason": "股份类别合计与登记总量不符，拒绝审核",
            "class_problems": problems,
        }

    revision.status = models.ST_APPROVED
    revision.reviewed_by = user
    if note:
        revision.change_note = f"{revision.change_note} | 审核备注：{note}"
    await db.flush()
    return {"ok": True, "approved": True}


async def review_generic(
    revision: models.Revision, approve: bool, user: str, note: str | None
) -> dict:
    if revision.status not in (models.ST_DRAFT, models.ST_REVIEW):
        return {"ok": False, "reason": f"修订状态为 {revision.status}，不可审核"}
    revision.status = models.ST_APPROVED if approve else models.ST_REJECTED
    revision.reviewed_by = user
    if note:
        revision.change_note = f"{revision.change_note} | 审核备注：{note}"
    return {"ok": True, "approved": approve}
