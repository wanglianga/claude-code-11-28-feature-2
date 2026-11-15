"""财务事实导入与审核：幂等、修订留痕、重述不改原值。

- 利润表、决议均走"导入(draft) -> 审核员审批(approved)"流程。
- 重述：新表 staging=restated，指向被重述的旧表；审批后旧表标记 superseded 但保留原金额，
  新表标记 restated。历史审计快照的 frozen_json 从不被重写。
- 所有导入按 source_ref + 指纹幂等。
"""
import hashlib
import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models
from .finance_schemas import (
    DividendResolutionIn,
    EquitySnapshotIn,
    FxRateIn,
    ProfitStatementIn,
    ReceiptIn,
    ReceivableIn,
)
from .services.revisions import create_revision


def fingerprint(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def import_profit_statement(db: AsyncSession, body: ProfitStatementIn, user: str) -> dict:
    fp = body.content_fingerprint or fingerprint(body.model_dump(mode="json"))
    existing = await db.scalar(
        select(models.ProfitStatement).where(
            models.ProfitStatement.source_ref == body.source_ref,
            models.ProfitStatement.content_fingerprint == fp,
        )
    )
    if existing:
        return {"ok": True, "duplicate": True, "statement_id": existing.id,
                "status": existing.status,
                "message": "相同来源标识与指纹的利润表已导入，幂等跳过"}

    restates_id = None
    if body.restates_source_ref:
        old = await db.scalar(
            select(models.ProfitStatement).where(
                models.ProfitStatement.source_ref == body.restates_source_ref,
                models.ProfitStatement.staging == "original",
            ).order_by(models.ProfitStatement.id.desc())
        )
        if not old:
            return {"ok": False, "reason": f"被重述的利润表 {body.restates_source_ref} 不存在"}
        restates_id = old.id

    stmt = models.ProfitStatement(
        company_subject_id=body.company_subject_id,
        source_ref=body.source_ref,
        content_fingerprint=fp,
        period_code=body.period_code,
        period_start=body.period_start,
        period_end=body.period_end,
        currency=body.currency,
        staging=body.staging,
        restates_statement_id=restates_id,
        provenance=body.provenance,
        status=models.FIN_DRAFT,
    )
    db.add(stmt)
    await db.flush()
    for ln in body.lines:
        db.add(models.ProfitLine(
            statement_id=stmt.id, line_no=ln.line_no,
            line_category=ln.line_category, label=ln.label,
            amount=ln.amount, payer_company_subject_id=ln.payer_company_subject_id,
            share_class=ln.share_class, provenance=ln.provenance,
        ))
    rev = await create_revision(
        db, models.REV_PROFIT, stmt.id,
        f"导入利润表 {body.source_ref}（{body.period_code}，"
        f"{'重述' if restates_id else '原始'}）", user,
    )
    stmt.revision_id = rev.id
    await db.flush()
    return {"ok": True, "duplicate": False, "statement_id": stmt.id,
            "status": stmt.status, "revision_id": rev.id,
            "restates_statement_id": restates_id,
            "message": "利润表已导入，待审核员审批"}


async def approve_profit_statement(db: AsyncSession, stmt: models.ProfitStatement,
                                   user: str) -> dict:
    """审批利润表；若为重述，旧表标记 superseded（保留原值）。"""
    if stmt.status == models.FIN_APPROVED or stmt.status == models.FIN_RESTATED:
        return {"ok": True, "already": True, "status": stmt.status}
    if stmt.restates_statement_id:
        old = await db.get(models.ProfitStatement, stmt.restates_statement_id)
        if old and old.status in (models.FIN_APPROVED, models.FIN_SUPERSEDED):
            old.status = models.FIN_SUPERSEDED   # 原值保留，不动金额
        if stmt.staging != "restated":
            stmt.staging = "restated"
        stmt.status = models.FIN_RESTATED
    else:
        stmt.status = models.FIN_APPROVED
    rev = await db.get(models.Revision, stmt.revision_id)
    if rev:
        rev.status = models.ST_APPROVED
        rev.reviewed_by = user
    await db.flush()
    return {"ok": True, "already": False, "status": stmt.status}


async def import_snapshot(db: AsyncSession, body: EquitySnapshotIn, user: str) -> dict:
    existing = await db.scalar(
        select(models.EquitySnapshot).where(
            models.EquitySnapshot.company_subject_id == body.company_subject_id,
            models.EquitySnapshot.snapshot_date == body.snapshot_date,
            models.EquitySnapshot.source_ref == body.source_ref,
        )
    )
    if existing:
        return {"ok": True, "duplicate": True, "snapshot_id": existing.id}
    snap = models.EquitySnapshot(
        company_subject_id=body.company_subject_id,
        snapshot_date=body.snapshot_date,
        source_ref=body.source_ref,
        status=body.status,
        provenance=body.provenance,
    )
    db.add(snap)
    await db.flush()
    for h in body.holdings:
        ratio = (h.shares / h.class_total_shares) if h.class_total_shares else Decimal("0")
        db.add(models.EquitySnapshotHolding(
            snapshot_id=snap.id, holder_subject_id=h.holder_subject_id,
            holder_label=h.holder_label, share_class=h.share_class,
            shares=h.shares, class_total_shares=h.class_total_shares,
            ratio=ratio.quantize(Decimal("0.00000001")),
        ))
    await db.flush()
    return {"ok": True, "duplicate": False, "snapshot_id": snap.id, "status": snap.status}


async def import_resolution(db: AsyncSession, body: DividendResolutionIn, user: str) -> dict:
    existing = await db.scalar(
        select(models.DividendResolution).where(
            models.DividendResolution.resolution_no == body.resolution_no)
    )
    if existing:
        return {"ok": True, "duplicate": True, "resolution_id": existing.id}
    res = models.DividendResolution(
        company_subject_id=body.company_subject_id,
        resolution_no=body.resolution_no,
        declaration_date=body.declaration_date,
        record_date=body.record_date,
        share_class=body.share_class,
        total_declared_amount=body.total_declared_amount,
        currency=body.currency,
        funding_period_code=body.funding_period_code,
        provenance=body.provenance,
        status=models.FIN_DRAFT,
    )
    db.add(res)
    await db.flush()
    for ln in body.lines:
        db.add(models.ResolutionLine(
            resolution_id=res.id, line_no=ln.line_no,
            holder_subject_id=ln.holder_subject_id, holder_label=ln.holder_label,
            share_class=ln.share_class, declared_amount=ln.declared_amount,
            currency=ln.currency, provenance=ln.provenance,
        ))
    rev = await create_revision(
        db, models.REV_RESOLUTION, res.id,
        f"导入分红决议 {body.resolution_no}", user)
    res.revision_id = rev.id
    await db.flush()
    return {"ok": True, "duplicate": False, "resolution_id": res.id,
            "status": res.status, "revision_id": rev.id}


async def approve_resolution(db: AsyncSession, res: models.DividendResolution, user: str) -> dict:
    if res.status == models.FIN_APPROVED:
        return {"ok": True, "already": True}
    res.status = models.FIN_APPROVED
    rev = await db.get(models.Revision, res.revision_id)
    if rev:
        rev.status = models.ST_APPROVED
        rev.reviewed_by = user
    await db.flush()
    return {"ok": True, "already": False}


async def import_receivable(db: AsyncSession, body: ReceivableIn, user: str) -> dict:
    existing = await db.scalar(
        select(models.ReceivableNotice).where(
            models.ReceivableNotice.source_ref == body.source_ref,
            models.ReceivableNotice.receivable_no == body.receivable_no,
        )
    )
    if existing:
        return {"ok": True, "duplicate": True, "receivable_id": existing.id}
    recv = models.ReceivableNotice(
        issuer_subject_id=body.issuer_subject_id,
        collecting_subject_id=body.collecting_subject_id,
        receivable_no=body.receivable_no,
        amount=body.amount, currency=body.currency,
        confirmed_date=body.confirmed_date,
        resolution_id=body.resolution_id,
        resolution_line_id=body.resolution_line_id,
        source_ref=body.source_ref, provenance=body.provenance,
        status=models.FIN_APPROVED,
    )
    db.add(recv)
    await db.flush()
    return {"ok": True, "duplicate": False, "receivable_id": recv.id}


async def import_receipt(db: AsyncSession, body: ReceiptIn, user: str) -> dict:
    existing = await db.scalar(
        select(models.BankReceipt).where(
            models.BankReceipt.receipt_no == body.receipt_no,
            models.BankReceipt.source_ref == body.source_ref,
        )
    )
    if existing:
        return {"ok": True, "duplicate": True, "receipt_id": existing.id}
    receipt = models.BankReceipt(
        receipt_no=body.receipt_no,
        collecting_subject_id=body.collecting_subject_id,
        payer_subject_id=body.payer_subject_id,
        payer_label=body.payer_label,
        amount=body.amount, currency=body.currency,
        direction=body.direction, value_date=body.value_date,
        memo=body.memo, source_ref=body.source_ref, provenance=body.provenance,
    )
    db.add(receipt)
    await db.flush()
    return {"ok": True, "duplicate": False, "receipt_id": receipt.id}


async def import_fx_rate(db: AsyncSession, body: FxRateIn, user: str) -> dict:
    existing = await db.scalar(
        select(models.FxRate).where(
            models.FxRate.from_currency == body.from_currency,
            models.FxRate.to_currency == body.to_currency,
            models.FxRate.source_ref == body.source_ref,
        )
    )
    if existing:
        return {"ok": True, "duplicate": True, "fx_rate_id": existing.id}
    if body.approved and body.rate is None:
        return {"ok": False, "reason": "标记为获批的汇率必须提供 rate"}
    row = models.FxRate(
        from_currency=body.from_currency, to_currency=body.to_currency,
        rate=body.rate, approved=body.approved, source_ref=body.source_ref,
        provenance=body.provenance, effective_date=body.effective_date,
    )
    db.add(row)
    await db.flush()
    return {"ok": True, "duplicate": False, "fx_rate_id": row.id}
