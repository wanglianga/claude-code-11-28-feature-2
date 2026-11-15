"""财务事实层路由：利润表、股权快照、决议、应收、回单、汇率、对账、审计快照。

只读发布与事实登记；不执行分红批准、现金预留、银行付款、退款或余额扣还。
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models
from ..auth import current_user, require_roles
from ..db import get_db
from ..finance_ingest import (
    approve_profit_statement,
    approve_resolution,
    import_fx_rate,
    import_profit_statement,
    import_receipt,
    import_receivable,
    import_resolution,
    import_snapshot,
)
from ..finance_schemas import (
    DividendResolutionIn,
    EquitySnapshotIn,
    FxRateIn,
    ProfitStatementIn,
    ReceiptIn,
    ReceivableIn,
    ReconCreateIn,
)
from ..services.finance.reconciliation import build_reconciliation, seal_audit_snapshot
from ..services.finance.periods import Period, cross_period, timeline_entry

router = APIRouter(prefix="/api/finance", tags=["finance"])


# ---------- 利润表 ----------
@router.post("/profit-statements")
async def post_profit_statement(
    body: ProfitStatementIn, db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("analyst")),
):
    outcome = await import_profit_statement(db, body, user["username"])
    if not outcome["ok"]:
        raise HTTPException(status_code=409, detail=outcome)
    await db.commit()
    return outcome


@router.get("/profit-statements")
async def list_profit_statements(db: AsyncSession = Depends(get_db),
                                 user: dict = Depends(current_user)):
    rows = list(await db.scalars(
        select(models.ProfitStatement).order_by(models.ProfitStatement.id.desc())))
    out = []
    for st in rows:
        company = await db.get(models.Subject, st.company_subject_id)
        lines = list(await db.scalars(
            select(models.ProfitLine).where(models.ProfitLine.statement_id == st.id)
            .order_by(models.ProfitLine.line_no)))
        out.append({
            "id": st.id, "company_subject_id": st.company_subject_id,
            "company_name": company.name if company else None,
            "source_ref": st.source_ref, "period_code": st.period_code,
            "period_start": st.period_start, "period_end": st.period_end,
            "currency": st.currency, "status": st.status, "staging": st.staging,
            "restates_statement_id": st.restates_statement_id,
            "provenance": st.provenance,
            "total_positive": str(sum((ln.amount for ln in lines if ln.amount > 0), 0)),
            "lines": [{
                "id": ln.id, "line_no": ln.line_no, "line_category": ln.line_category,
                "label": ln.label, "amount": str(ln.amount),
                "payer_company_subject_id": ln.payer_company_subject_id,
                "share_class": ln.share_class, "provenance": ln.provenance,
            } for ln in lines],
        })
    return out


@router.post("/profit-statements/{statement_id}/approve")
async def approve_profit(statement_id: int, db: AsyncSession = Depends(get_db),
                         user: dict = Depends(require_roles("reviewer"))):
    stmt = await db.get(models.ProfitStatement, statement_id)
    if not stmt:
        raise HTTPException(status_code=404, detail="利润表不存在")
    outcome = await approve_profit_statement(db, stmt, user["username"])
    await db.commit()
    return outcome


# ---------- 股权快照 ----------
@router.post("/equity-snapshots")
async def post_snapshot(body: EquitySnapshotIn, db: AsyncSession = Depends(get_db),
                        user: dict = Depends(require_roles("analyst"))):
    outcome = await import_snapshot(db, body, user["username"])
    await db.commit()
    return outcome


@router.get("/equity-snapshots")
async def list_snapshots(db: AsyncSession = Depends(get_db),
                         user: dict = Depends(current_user)):
    rows = list(await db.scalars(select(models.EquitySnapshot)
                                 .order_by(models.EquitySnapshot.id.desc())))
    out = []
    for s in rows:
        company = await db.get(models.Subject, s.company_subject_id)
        holdings = list(await db.scalars(
            select(models.EquitySnapshotHolding).where(
                models.EquitySnapshotHolding.snapshot_id == s.id)))
        out.append({
            "id": s.id, "company_subject_id": s.company_subject_id,
            "company_name": company.name if company else None,
            "snapshot_date": s.snapshot_date, "source_ref": s.source_ref,
            "status": s.status, "provenance": s.provenance,
            "holdings": [{
                "id": h.id, "holder_subject_id": h.holder_subject_id,
                "holder_label": h.holder_label, "share_class": h.share_class,
                "shares": str(h.shares), "class_total_shares": str(h.class_total_shares),
                "ratio": str(h.ratio),
            } for h in holdings],
        })
    return out


# ---------- 决议 ----------
@router.post("/resolutions")
async def post_resolution(body: DividendResolutionIn, db: AsyncSession = Depends(get_db),
                          user: dict = Depends(require_roles("analyst"))):
    outcome = await import_resolution(db, body, user["username"])
    if not outcome["ok"]:
        raise HTTPException(status_code=409, detail=outcome)
    await db.commit()
    return outcome


@router.get("/resolutions")
async def list_resolutions(db: AsyncSession = Depends(get_db),
                           user: dict = Depends(current_user)):
    rows = list(await db.scalars(select(models.DividendResolution)
                                 .order_by(models.DividendResolution.id.desc())))
    out = []
    for r in rows:
        company = await db.get(models.Subject, r.company_subject_id)
        lines = list(await db.scalars(
            select(models.ResolutionLine).where(models.ResolutionLine.resolution_id == r.id)
            .order_by(models.ResolutionLine.line_no)))
        out.append({
            "id": r.id, "company_subject_id": r.company_subject_id,
            "company_name": company.name if company else None,
            "resolution_no": r.resolution_no,
            "declaration_date": r.declaration_date, "record_date": r.record_date,
            "share_class": r.share_class,
            "total_declared_amount": str(r.total_declared_amount),
            "currency": r.currency, "funding_period_code": r.funding_period_code,
            "status": r.status, "provenance": r.provenance,
            "lines": [{
                "id": ln.id, "line_no": ln.line_no, "holder_subject_id": ln.holder_subject_id,
                "holder_label": ln.holder_label, "share_class": ln.share_class,
                "declared_amount": str(ln.declared_amount), "currency": ln.currency,
                "provenance": ln.provenance,
            } for ln in lines],
        })
    return out


@router.post("/resolutions/{resolution_id}/approve")
async def approve_res(resolution_id: int, db: AsyncSession = Depends(get_db),
                      user: dict = Depends(require_roles("reviewer"))):
    res = await db.get(models.DividendResolution, resolution_id)
    if not res:
        raise HTTPException(status_code=404, detail="决议不存在")
    outcome = await approve_resolution(db, res, user["username"])
    await db.commit()
    return outcome


# ---------- 应收 / 回单 / 汇率 ----------
@router.post("/receivables")
async def post_receivable(body: ReceivableIn, db: AsyncSession = Depends(get_db),
                          user: dict = Depends(require_roles("analyst"))):
    outcome = await import_receivable(db, body, user["username"])
    await db.commit()
    return outcome


@router.get("/receivables")
async def list_receivables(db: AsyncSession = Depends(get_db),
                           user: dict = Depends(current_user)):
    rows = list(await db.scalars(select(models.ReceivableNotice)
                                 .order_by(models.ReceivableNotice.id.desc())))
    out = []
    for r in rows:
        issuer = await db.get(models.Subject, r.issuer_subject_id)
        collector = await db.get(models.Subject, r.collecting_subject_id)
        out.append({
            "id": r.id, "receivable_no": r.receivable_no,
            "issuer_subject_id": r.issuer_subject_id,
            "issuer_name": issuer.name if issuer else None,
            "collecting_subject_id": r.collecting_subject_id,
            "collecting_name": collector.name if collector else None,
            "amount": str(r.amount), "currency": r.currency,
            "confirmed_date": r.confirmed_date,
            "resolution_id": r.resolution_id,
            "resolution_line_id": r.resolution_line_id,
            "source_ref": r.source_ref, "provenance": r.provenance,
            "status": r.status,
        })
    return out


@router.post("/receipts")
async def post_receipt(body: ReceiptIn, db: AsyncSession = Depends(get_db),
                       user: dict = Depends(require_roles("analyst"))):
    outcome = await import_receipt(db, body, user["username"])
    await db.commit()
    return outcome


@router.get("/receipts")
async def list_receipts(db: AsyncSession = Depends(get_db),
                        user: dict = Depends(current_user)):
    rows = list(await db.scalars(select(models.BankReceipt)
                                 .order_by(models.BankReceipt.id.desc())))
    out = []
    for b in rows:
        collector = await db.get(models.Subject, b.collecting_subject_id)
        out.append({
            "id": b.id, "receipt_no": b.receipt_no,
            "collecting_subject_id": b.collecting_subject_id,
            "collecting_name": collector.name if collector else None,
            "payer_subject_id": b.payer_subject_id, "payer_label": b.payer_label,
            "amount": str(b.amount), "currency": b.currency,
            "direction": b.direction, "value_date": b.value_date,
            "memo": b.memo, "source_ref": b.source_ref, "provenance": b.provenance,
        })
    return out


@router.post("/fx-rates")
async def post_fx_rate(body: FxRateIn, db: AsyncSession = Depends(get_db),
                       user: dict = Depends(require_roles("reviewer"))):
    outcome = await import_fx_rate(db, body, user["username"])
    if not outcome["ok"]:
        raise HTTPException(status_code=409, detail=outcome)
    await db.commit()
    return outcome


@router.get("/fx-rates")
async def list_fx_rates(db: AsyncSession = Depends(get_db),
                        user: dict = Depends(current_user)):
    rows = list(await db.scalars(select(models.FxRate).order_by(models.FxRate.id.desc())))
    return [{
        "id": r.id, "from_currency": r.from_currency, "to_currency": r.to_currency,
        "rate": str(r.rate) if r.rate is not None else None,
        "approved": r.approved, "source_ref": r.source_ref,
        "provenance": r.provenance,
        "effective_date": r.effective_date,
    } for r in rows]


# ---------- 对账运行 ----------
@router.post("/reconciliation-runs")
async def create_recon(body: ReconCreateIn, db: AsyncSession = Depends(get_db),
                       user: dict = Depends(require_roles("analyst"))):
    outcome = await build_reconciliation(db, body.as_of_date, user["username"])
    await db.commit()
    return outcome


@router.get("/reconciliation-runs")
async def list_recons(db: AsyncSession = Depends(get_db),
                      user: dict = Depends(current_user)):
    rows = list(await db.scalars(select(models.ReconciliationRun)
                                 .order_by(models.ReconciliationRun.id.desc()).limit(100)))
    return [{
        "id": r.id, "as_of_date": r.as_of_date, "status": r.status,
        "created_by": r.created_by, "created_at": r.created_at,
        "published_at": r.published_at,
    } for r in rows]


@router.get("/reconciliation-runs/{run_id}")
async def get_recon(run_id: int, db: AsyncSession = Depends(get_db),
                    user: dict = Depends(current_user)):
    run = await db.get(models.ReconciliationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="对账运行不存在")

    name_cache: dict[int, str] = {}

    async def name_of(sid):
        if sid is None:
            return None
        if sid not in name_cache:
            s = await db.get(models.Subject, sid)
            name_cache[sid] = s.name if s else str(sid)
        return name_cache[sid]

    chains = list(await db.scalars(
        select(models.SourceChain).where(models.SourceChain.run_id == run_id)
        .order_by(models.SourceChain.receivable_id, models.SourceChain.id)))
    chain_list = []
    for c in chains:
        chain_list.append({
            "id": c.id, "receivable_id": c.receivable_id,
            "entry_kind": c.entry_kind,
            "terminal_company_subject_id": c.terminal_company_subject_id,
            "terminal_company_name": await name_of(c.terminal_company_subject_id),
            "terminal_statement_id": c.terminal_statement_id,
            "terminal_line_id": c.terminal_line_id,
            "path_weight": c.path_weight, "amount": c.amount,
            "currency": c.currency,
            "path": json.loads(c.path_json or "[]"),
            "detail": json.loads(c.detail_json or "{}"),
        })

    matches = list(await db.scalars(
        select(models.ReceiptMatch).where(models.ReceiptMatch.run_id == run_id)
        .order_by(models.ReceiptMatch.id)))
    match_list = []
    for m in matches:
        receipt = await db.get(models.BankReceipt, m.receipt_id)
        match_list.append({
            "id": m.id, "receipt_id": m.receipt_id,
            "receipt_no": receipt.receipt_no if receipt else None,
            "receivable_id": m.receivable_id, "rule": m.rule,
            "status": m.status, "matched_amount": m.matched_amount,
            "currency": m.currency, "verified": m.verified,
            "reason": m.reason, "detail": json.loads(m.detail_json or "{}"),
        })

    bridges = list(await db.scalars(
        select(models.BridgeLayer).where(models.BridgeLayer.run_id == run_id)
        .order_by(models.BridgeLayer.receivable_id, models.BridgeLayer.id)))
    bridge_list = [{
        "id": b.id, "receivable_id": b.receivable_id, "layer": b.layer,
        "amount": b.amount, "currency": b.currency,
        "reporting_amount": b.reporting_amount,
        "reporting_currency": b.reporting_currency,
        "fx_status": b.fx_status,
        "detail": json.loads(b.detail_json or "{}"),
    } for b in bridges]

    # 应收摘要
    recv_rows = list(await db.scalars(
        select(models.ReceivableNotice).where(
            models.ReceivableNotice.confirmed_date <= run.as_of_date,
            models.ReceivableNotice.status == models.FIN_APPROVED)))
    receivable_list = []
    for r in recv_rows:
        receivable_list.append({
            "id": r.id, "receivable_no": r.receivable_no,
            "issuer_subject_id": r.issuer_subject_id,
            "issuer_name": await name_of(r.issuer_subject_id),
            "collecting_subject_id": r.collecting_subject_id,
            "collecting_name": await name_of(r.collecting_subject_id),
            "amount": r.amount, "currency": r.currency,
            "confirmed_date": r.confirmed_date,
            "resolution_id": r.resolution_id,
        })

    audit = await db.scalar(
        select(models.AuditSnapshot).where(models.AuditSnapshot.run_id == run_id))
    audit_out = None
    if audit:
        audit_out = {
            "id": audit.id, "as_of_date": audit.as_of_date,
            "seal_hash": audit.seal_hash, "created_by": audit.created_by,
            "created_at": audit.created_at,
            "frozen": json.loads(audit.frozen_json or "{}"),
        }

    # 分层汇总：按币种分别累计，不混合币种；缺失金额的层计入 missing，不倒成 0
    layer_totals: dict[str, dict] = {}
    layer_missing: dict[str, list] = {}
    for b in bridge_list:
        key = b["layer"]
        if b["amount"] is None:
            layer_missing.setdefault(key, []).append({
                "receivable_id": b["receivable_id"], "currency": b["currency"],
                "fx_status": b["fx_status"], "detail": b["detail"],
            })
            continue
        bucket = layer_totals.setdefault(key, {})
        cur = b["currency"]
        bucket[cur] = round(float(bucket.get(cur, 0)) + float(b["amount"]), 4)

    return {
        "id": run.id, "as_of_date": run.as_of_date, "status": run.status,
        "diagnostics": json.loads(run.diagnostics or "{}"),
        "created_by": run.created_by, "created_at": run.created_at,
        "published_by": run.published_by, "published_at": run.published_at,
        "receivables": receivable_list,
        "chains": chain_list,
        "matches": match_list,
        "bridges": bridge_list,
        "layer_totals": layer_totals,
        "layer_missing": layer_missing,
        "audit_snapshot": audit_out,
    }


@router.post("/reconciliation-runs/{run_id}/publish")
async def publish_recon(run_id: int, db: AsyncSession = Depends(get_db),
                        user: dict = Depends(require_roles("reviewer"))):
    run = await db.get(models.ReconciliationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="对账运行不存在")
    if run.status == models.RECON_SEALED:
        raise HTTPException(status_code=409, detail="已封存审计快照，不可再发布")
    if run.status == models.RECON_PUBLISHED:
        raise HTTPException(status_code=409, detail="运行已发布")
    from datetime import datetime, timezone
    run.status = models.RECON_PUBLISHED
    run.published_by = user["username"]
    run.published_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(run)
    return {"id": run.id, "status": run.status}


@router.post("/reconciliation-runs/{run_id}/seal")
async def seal_recon(run_id: int, db: AsyncSession = Depends(get_db),
                     user: dict = Depends(require_roles("reviewer"))):
    """按指定截止日封存利润来源、股权版本、决议资料与匹配结果；只读。"""
    run = await db.get(models.ReconciliationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="对账运行不存在")
    existing = await db.scalar(
        select(models.AuditSnapshot).where(models.AuditSnapshot.run_id == run_id))
    if existing:
        raise HTTPException(status_code=409, detail="该运行已封存审计快照，不可重复封存")
    snap = await seal_audit_snapshot(db, run, user["username"])
    await db.commit()
    return {
        "id": snap.id, "run_id": run_id, "as_of_date": snap.as_of_date,
        "seal_hash": snap.seal_hash, "created_by": snap.created_by,
        "created_at": snap.created_at,
    }


@router.get("/audit-snapshots")
async def list_audit_snapshots(db: AsyncSession = Depends(get_db),
                               user: dict = Depends(current_user)):
    rows = list(await db.scalars(select(models.AuditSnapshot)
                                 .order_by(models.AuditSnapshot.id.desc())))
    return [{
        "id": a.id, "run_id": a.run_id, "as_of_date": a.as_of_date,
        "seal_hash": a.seal_hash, "created_by": a.created_by,
        "created_at": a.created_at,
    } for a in rows]


# ---------- 跨期时间线 ----------
@router.get("/timeline")
async def finance_timeline(db: AsyncSession = Depends(get_db),
                           user: dict = Depends(current_user)):
    """分别列出四类日期事实，证明它们不是同一会计事实。"""
    stmts = list(await db.scalars(select(models.ProfitStatement)))
    resolutions = list(await db.scalars(select(models.DividendResolution)))
    receivables = list(await db.scalars(select(models.ReceivableNotice)))
    receipts = list(await db.scalars(select(models.BankReceipt)))

    periods = [Period(s.period_code, s.period_start, s.period_end) for s in stmts]
    entries = []
    for s in stmts:
        company = await db.get(models.Subject, s.company_subject_id)
        entries.append(timeline_entry(
            "profit_period", s.period_end,
            f"利润表 {s.period_code}（{s.staging}）",
            {"statement_id": s.id, "company_name": company.name if company else None,
             "period_start": s.period_start.isoformat(),
             "period_end": s.period_end.isoformat(), "status": s.status}))
    for r in resolutions:
        company = await db.get(models.Subject, r.company_subject_id)
        entries.append(timeline_entry(
            "resolution_record_date", r.record_date,
            f"决议记录日 {r.resolution_no}",
            {"resolution_id": r.id, "company_name": company.name if company else None,
             "declaration_date": r.declaration_date.isoformat()}))
    for rc in receivables:
        collector = await db.get(models.Subject, rc.collecting_subject_id)
        entries.append(timeline_entry(
            "receivable_confirmed_date", rc.confirmed_date,
            f"应收确认 {rc.receivable_no}",
            {"receivable_id": rc.id, "collecting_name": collector.name if collector else None,
             "amount": str(rc.amount), "currency": rc.currency}))
    for b in receipts:
        collector = await db.get(models.Subject, b.collecting_subject_id)
        entries.append(timeline_entry(
            "receipt_value_date", b.value_date,
            f"回单到账 {b.receipt_no}",
            {"receipt_id": b.id, "collecting_name": collector.name if collector else None,
             "amount": str(b.amount), "currency": b.currency}))

    entries.sort(key=lambda e: (e["date"] or "", e["kind"]))

    # 跨期示例：每个应收与其已核验回单的期间对比（仅展示期间边界事实）
    comparisons = []
    for rc in receivables:
        for b in receipts:
            if b.collecting_subject_id != rc.collecting_subject_id:
                continue
            if b.currency != rc.currency:
                continue
            info = cross_period(b.value_date, rc.confirmed_date, periods)
            if info["cross"]:
                comparisons.append({
                    "receivable_no": rc.receivable_no,
                    "receipt_no": b.receipt_no,
                    **info,
                    "receivable_confirmed_date": rc.confirmed_date.isoformat(),
                    "receipt_value_date": b.value_date.isoformat(),
                })
    return {
        "entries": entries,
        "cross_period_facts": comparisons,
        "note": ("利润表按会计期间、决议按记录日、应收按确认日、回单按到账日分别建模；"
                 "同一自然月不视为同一会计事实"),
    }
