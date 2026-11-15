"""对账编排：来源分配、回单匹配、跨期桥接、审计封存的持久化层。

所有"事实"（差异、候选、残差、缺失汇率）都带可追溯的引用：
利润表行 id、决议行 id、股权快照 id、匹配规则、回单 id。
不执行分红批准、现金预留、银行付款、退款或余额扣还。
"""
import hashlib
import json
from datetime import date
from decimal import Decimal
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ... import models
from .allocation import (
    KIND_TITLE_PENDING,
    build_statement_index,
    load_statement_views,
    select_funding_statement,
    title_pending_chain,
    to_fraction,
    trace_sources,
)
from .currency import FxView, convert, translation_difference
from .matching import (
    ReceiptView,
    ReceivableView,
    detect_split_and_aggregate,
    match_receipts,
)
from .periods import Period


def _dec(fr: Fraction) -> Decimal:
    return (Decimal(fr.numerator) / Decimal(fr.denominator)).quantize(Decimal("0.0001"))


def _frac_str(fr: Fraction) -> str:
    return f"{Decimal(fr.numerator) / Decimal(fr.denominator):.10f}"


async def _load_finance_state(db: AsyncSession, as_of: date):
    statements = list(await db.scalars(
        select(models.ProfitStatement).where(
            models.ProfitStatement.period_end <= as_of)
    ))
    lines_by_stmt: dict[int, list[models.ProfitLine]] = {}
    for st in statements:
        lines_by_stmt[st.id] = list(await db.scalars(
            select(models.ProfitLine).where(models.ProfitLine.statement_id == st.id)
            .order_by(models.ProfitLine.line_no)
        ))
    views = load_statement_views(statements, lines_by_stmt)

    resolutions = list(await db.scalars(
        select(models.DividendResolution).where(
            models.DividendResolution.status.in_(
                [models.FIN_APPROVED, models.FIN_DRAFT]))
    ))
    res_lines: dict[int, list[models.ResolutionLine]] = {}
    for r in resolutions:
        res_lines[r.id] = list(await db.scalars(
            select(models.ResolutionLine).where(models.ResolutionLine.resolution_id == r.id)
            .order_by(models.ResolutionLine.line_no)
        ))

    snapshots = list(await db.scalars(
        select(models.EquitySnapshot).where(
            models.EquitySnapshot.snapshot_date <= as_of)
    ))
    snap_holdings: dict[int, list[models.EquitySnapshotHolding]] = {}
    for s in snapshots:
        snap_holdings[s.id] = list(await db.scalars(
            select(models.EquitySnapshotHolding).where(
                models.EquitySnapshotHolding.snapshot_id == s.id)
        ))

    receivables = list(await db.scalars(
        select(models.ReceivableNotice).where(
            models.ReceivableNotice.confirmed_date <= as_of,
            models.ReceivableNotice.status == models.FIN_APPROVED)
    ))
    receipts = list(await db.scalars(
        select(models.BankReceipt).where(models.BankReceipt.value_date <= as_of)
    ))
    fx = list(await db.scalars(select(models.FxRate)))
    return {
        "statements": statements, "lines": lines_by_stmt, "views": views,
        "resolutions": resolutions, "res_lines": res_lines,
        "snapshots": snapshots, "snap_holdings": snap_holdings,
        "receivables": receivables, "receipts": receipts, "fx": fx,
    }


def _find_snapshot(snapshots, snap_holdings, company_id: int, record_date: date):
    """只认记录日当天、状态可确认的快照；缺失返回 None（不借用最近一期）。"""
    for s in snapshots:
        if s.company_subject_id == company_id and s.snapshot_date == record_date:
            if s.status == models.SNAP_CONFIRMABLE:
                return s, snap_holdings.get(s.id, [])
    return None


def _allocate_title(receivable, resolution, res_line, snapshots, snap_holdings):
    """把应收金额按记录日快照的股份类别权属拆成 (company_id -> amount) 或权属待证。"""
    if resolution is None or res_line is None:
        return None, title_pending_chain(
            to_fraction(receivable.amount),
            "应收未挂接决议行，无法确认上层权属来源",
            {"receivable_id": receivable.id},
        )

    found = _find_snapshot(
        snapshots, snap_holdings, resolution.company_subject_id, resolution.record_date)
    if found is None:
        return None, title_pending_chain(
            to_fraction(receivable.amount),
            ("决议记录日无可确认的股权快照，列为权属待证，"
             "不借用最近一期股权比例"),
            {"resolution_id": resolution.id,
             "record_date": resolution.record_date.isoformat(),
             "company_subject_id": resolution.company_subject_id},
        )

    snapshot, holdings = found
    class_entries = [h for h in holdings if h.share_class == res_line.share_class]
    if not class_entries:
        return None, title_pending_chain(
            to_fraction(receivable.amount),
            f"快照中无 {res_line.share_class} 类别的持股记录，无法按股份类别确认权属",
            {"snapshot_id": snapshot.id, "share_class": res_line.share_class},
        )
    # 收款主体必须在该记录日快照的对应股份类别中真实出现，否则权属无据
    collector_in_snapshot = any(
        h.holder_subject_id == receivable.collecting_subject_id for h in class_entries)
    if not collector_in_snapshot:
        return None, title_pending_chain(
            to_fraction(receivable.amount),
            ("收款主体未出现在记录日股权快照的对应股份类别中，"
             "权属待证，不借用其他主体比例"),
            {"snapshot_id": snapshot.id,
             "record_date": resolution.record_date.isoformat(),
             "share_class": res_line.share_class,
             "collecting_subject_id": receivable.collecting_subject_id},
        )
    return {"snapshot": snapshot, "holdings": class_entries}, None


async def build_reconciliation(
    db: AsyncSession, as_of: date, user: str,
) -> dict:
    state = await _load_finance_state(db, as_of)
    index = build_statement_index(state["views"])

    periods: list[Period] = []
    for st in state["views"]:
        periods.append(Period(st.period_code, st.period_start, st.period_end))
    periods = sorted({(p.code, p.start, p.end): p for p in periods}.values(),
                     key=lambda p: (p.start, p.end))

    run = models.ReconciliationRun(
        as_of_date=as_of, status=models.RECON_DRAFT, created_by=user,
    )
    db.add(run)
    await db.flush()

    # ---------- 决议行 -> 快照权属 ----------
    snap_by_company_date = {}
    for s in state["snapshots"]:
        if s.status == models.SNAP_CONFIRMABLE:
            snap_by_company_date[(s.company_subject_id, s.snapshot_date)] = s

    title_cache: dict[int, tuple] = {}
    for receivable in state["receivables"]:
        resolution = next((r for r in state["resolutions"]
                           if r.id == receivable.resolution_id), None)
        res_line = None
        if resolution and receivable.resolution_line_id:
            res_line = next((rl for rl in state["res_lines"].get(resolution.id, [])
                             if rl.id == receivable.resolution_line_id), None)
        title_cache[receivable.id] = _allocate_title(
            receivable, resolution, res_line, state["snapshots"], state["snap_holdings"])

    # ---------- 来源链 ----------
    pending_titles: list[dict] = []
    all_chains: list[dict] = []
    for receivable in state["receivables"]:
        title, pending = title_cache[receivable.id]
        if pending is not None:
            pending_titles.append({
                "receivable_id": receivable.id,
                "receivable_no": receivable.receivable_no,
                "amount": str(_dec(to_fraction(receivable.amount))),
                "currency": receivable.currency,
                **pending["detail"],
            })
            # 权属待证链也落库（kind=title_pending, amount 该项全部）
            db.add(models.SourceChain(
                run_id=run.id, receivable_id=receivable.id,
                entry_kind=KIND_TITLE_PENDING, path_json="[]",
                terminal_company_subject_id=None, terminal_statement_id=None,
                terminal_line_id=None, path_weight=Decimal("1"),
                amount=_dec(to_fraction(receivable.amount)),
                currency=receivable.currency,
                detail_json=json.dumps(pending["detail"], ensure_ascii=False),
            ))
            continue

        snapshot = title["snapshot"]
        resolution = next(r for r in state["resolutions"]
                          if r.id == receivable.resolution_id)
        funding_period = resolution.funding_period_code
        receiver_amount = to_fraction(receivable.amount)
        # 按快照中同类别持股比例，将该应收拆给各上层持有人；
        # 但本应收本就是某持有人的应收，只取该持有人比例用于验证权属，余下按路径贡献保留。
        holder_entry = next(
            (h for h in title["holdings"] if h.holder_subject_id == receivable.collecting_subject_id),
            None,
        )
        claim_ratio = to_fraction(holder_entry.ratio) if holder_entry else Fraction(0)
        expected_amount = receiver_amount  # 应收金额按通知为准
        if funding_period is None:
            db.add(models.SourceChain(
                run_id=run.id, receivable_id=receivable.id,
                entry_kind="unallocatable", path_json="[]",
                terminal_company_subject_id=None, terminal_statement_id=None,
                terminal_line_id=None, path_weight=Decimal("0"),
                amount=_dec(expected_amount), currency=receivable.currency,
                detail_json=json.dumps({
                    "reason": "决议未指定资金来源会计期间，无法定位利润表",
                    "resolution_id": resolution.id,
                }, ensure_ascii=False),
            ))
            continue

        # 收款方为上层公司：该公司的利润来源中含对该子公司的分红收益。
        # 从付款公司（子公司/经营公司）的利润表向下穿透。
        issuer_id = receivable.issuer_subject_id
        chains = trace_sources(
            expected_amount, issuer_id, funding_period, index,
            visited=frozenset({issuer_id}), path=(),
        )
        # 逐链落库；把应收的"经决议行 -> 快照 -> 付款公司路径"前置到 path
        for ch in chains:
            weight = _path_weight_from_detail(ch)
            detail = {
                **ch["detail"],
                "receivable_id": receivable.id,
                "receivable_no": receivable.receivable_no,
                "resolution_id": resolution.id,
                "resolution_line_id": receivable.resolution_line_id,
                "snapshot_id": snapshot.id,
                "snapshot_date": snapshot.snapshot_date.isoformat(),
                "record_date": resolution.record_date.isoformat(),
                "claim_ratio_of_collecting_holder": _frac_str(claim_ratio),
                "funding_period_code": funding_period,
                "confirmed_date": receivable.confirmed_date.isoformat(),
            }
            terminal_line = ch["path"][-1]["line_id"] if ch["path"] else None
            terminal_stmt = ch["path"][-1]["statement_id"] if ch["path"] else None
            terminal_company = ch["path"][-1]["company_subject_id"] if ch["path"] else None
            chain_amount = ch["amount"]
            db.add(models.SourceChain(
                run_id=run.id, receivable_id=receivable.id,
                entry_kind=ch["kind"], path_json=json.dumps(ch["path"], ensure_ascii=False),
                terminal_company_subject_id=terminal_company,
                terminal_statement_id=terminal_stmt, terminal_line_id=terminal_line,
                path_weight=Decimal(str(weight)),
                amount=_dec(chain_amount), currency=receivable.currency,
                detail_json=json.dumps(detail, ensure_ascii=False),
            ))
            all_chains.append({
                "receivable_id": receivable.id, "kind": ch["kind"],
                "amount": _dec(chain_amount), "detail": detail,
            })

    # ---------- 回单匹配 ----------
    receivable_payer_map = {
        r.id: r.issuer_subject_id for r in state["receivables"]
    }
    recv_views = [
        ReceivableView(
            id=r.id, receivable_no=r.receivable_no,
            collecting_subject_id=r.collecting_subject_id,
            amount=to_fraction(r.amount), currency=r.currency,
            confirmed_date=r.confirmed_date, resolution_id=r.resolution_id,
            resolution_line_id=r.resolution_line_id,
        ) for r in state["receivables"]
    ]
    receipt_views = [
        ReceiptView(
            id=b.id, receipt_no=b.receipt_no,
            collecting_subject_id=b.collecting_subject_id,
            payer_subject_id=b.payer_subject_id, payer_label=b.payer_label,
            amount=to_fraction(b.amount), currency=b.currency, direction=b.direction,
            value_date=b.value_date, memo=b.memo or "",
        ) for b in state["receipts"]
    ]
    raw_matches = match_receipts(
        receipt_views, recv_views, receivable_payer_map,
    )
    # 对 verified 结果识别拆分/汇聚（仅在有把握时改标，不改金额）
    verified_matches = [m for m in raw_matches if m.status == "verified"]
    refined = detect_split_and_aggregate(verified_matches, receipt_views)
    # 把 refined 的改动并回 raw（按 receipt_id+receivable_id 键）
    refined_map = {(m.receipt_id, m.receivable_id): m for m in refined}
    final_matches = [
        refined_map.get((m.receipt_id, m.receivable_id), m) if m.status == "verified" else m
        for m in raw_matches
    ]

    verified_by_receivable: dict[int, Decimal] = {}
    excluded_receipt_amounts: list[dict] = []
    conflict_receivable_flags: dict[int, list[int]] = {}
    for m in final_matches:
        db.add(models.ReceiptMatch(
            run_id=run.id, receipt_id=m.receipt_id,
            receivable_id=m.receivable_id,
            rule=m.rule, status=m.status,
            matched_amount=_dec(m.matched_amount) if m.matched_amount else Decimal("0"),
            currency=m.currency, verified=m.verified,
            reason=m.reason,
            detail_json=json.dumps(m.detail, ensure_ascii=False),
        ))
        if m.verified and m.receivable_id is not None:
            verified_by_receivable[m.receivable_id] = (
                verified_by_receivable.get(m.receivable_id, Decimal("0"))
                + _dec(m.matched_amount)
            )
        if m.status == "one_to_many":
            excluded_receipt_amounts.append({
                "receipt_id": m.receipt_id,
                "receivable_id": m.receivable_id,
                "amount": str(_dec(m.matched_amount)),
                "currency": m.currency,
                "reason": m.reason,
            })
            conflict_receivable_flags.setdefault(
                m.receivable_id, []).append(m.receipt_id)

    # ---------- 无法归属（来源链合计 != 应收金额的差额） ----------
    chain_totals: dict[int, Fraction] = {}
    for ch in all_chains:
        chain_totals[ch["receivable_id"]] = (
            chain_totals.get(ch["receivable_id"], Fraction(0))
            + to_fraction(ch["amount"])
        )

    # ---------- 跨期桥接 ----------
    # 五层分别发布：已声明应收 / 可核验到账 / 汇率差异 / 重述影响 / 无法归属。
    # 金额按各自币种发布，不把不同币种混加；报告币仅在有获批汇率时给出。
    fx_views = [
        FxView(r.from_currency, r.to_currency, r.rate, r.approved, r.source_ref,
               r.effective_date)
        for r in state["fx"]
    ]
    for receivable in state["receivables"]:
        recv_fr = to_fraction(receivable.amount)
        resolution = next((r for r in state["resolutions"]
                           if r.id == receivable.resolution_id), None)

        # 1. 已声明应收（按确认日；报告币仅在 CNY 时等于原币）
        db.add(models.BridgeLayer(
            run_id=run.id, receivable_id=receivable.id,
            layer=models.BR_DECLARED, amount=_dec(recv_fr),
            currency=receivable.currency,
            reporting_amount=_dec(recv_fr) if receivable.currency == "CNY" else None,
            reporting_currency=("CNY" if receivable.currency == "CNY" else receivable.currency),
            detail_json=json.dumps({
                "receivable_no": receivable.receivable_no,
                "resolution_id": receivable.resolution_id,
                "confirmed_date": receivable.confirmed_date.isoformat(),
                "issuer_subject_id": receivable.issuer_subject_id,
            }, ensure_ascii=False),
        ))

        # 2. 可核验到账（按到账币种；报告币按到账日获批汇率）
        verified_amount = verified_by_receivable.get(receivable.id, Decimal("0"))
        fx_info = convert(verified_amount, receivable.currency, "CNY", fx_views,
                          as_of=_latest_value_date(state["receipts"]))
        db.add(models.BridgeLayer(
            run_id=run.id, receivable_id=receivable.id,
            layer=models.BR_VERIFIED, amount=verified_amount,
            currency=receivable.currency,
            reporting_amount=fx_info["reporting_amount"],
            reporting_currency="CNY",
            fx_status=fx_info["fx_status"],
            detail_json=json.dumps({
                "verified_amount_original": str(verified_amount),
                "fx_source": fx_info["fx_source"],
                "rate": fx_info["rate"],
                "message": fx_info["message"],
                "excluded_one_to_many": conflict_receivable_flags.get(receivable.id, []),
            }, ensure_ascii=False),
        ))

        # 3. 汇率差异（换算口径差异，仅外币且两端均有获批汇率时产生）
        if receivable.currency != "CNY" and verified_amount != 0:
            value_date = _value_date_for_receivable(state["receipts"], receivable)
            diff = translation_difference(
                verified_amount, receivable.currency, "CNY", fx_views,
                receivable.confirmed_date, value_date or receivable.confirmed_date,
            )
            if diff["status"] == "applied":
                db.add(models.BridgeLayer(
                    run_id=run.id, receivable_id=receivable.id,
                    layer=models.BR_FX, amount=diff["difference"], currency="CNY",
                    reporting_amount=diff["difference"], reporting_currency="CNY",
                    fx_status="applied",
                    detail_json=json.dumps({
                        "reason": "确认日与到账日采用不同获批汇率，产生换算口径差异",
                        "original_currency": receivable.currency,
                        "rate_at_recognition": diff["rate_at_recognition"],
                        "rate_at_value": diff["rate_at_value"],
                        "reporting_at_recognition": str(diff.get("reporting_at_recognition")),
                        "reporting_at_value": str(diff.get("reporting_at_value")),
                    }, ensure_ascii=False),
                ))
            else:
                db.add(models.BridgeLayer(
                    run_id=run.id, receivable_id=receivable.id,
                    layer=models.BR_FX, amount=None, currency=receivable.currency,
                    reporting_amount=None, reporting_currency="CNY",
                    fx_status="missing",
                    detail_json=json.dumps({
                        "reason": ("确认日或到账日缺少获批汇率来源，"
                                   "仅发布原币金额与缺失汇率提示，不自行换算"),
                        "fx_status": "missing",
                        "original_currency": receivable.currency,
                        "original_amount": str(verified_amount),
                    }, ensure_ascii=False),
                ))

        # 4. 重述影响：付款公司在审计后被重述的差额
        issuer_id = receivable.issuer_subject_id
        funding = resolution.funding_period_code if resolution else None
        restate = _restatement_delta(index, state["views"], issuer_id, funding)
        if restate is not None:
            stmt = select_funding_statement(index, issuer_id, funding)
            scale = (recv_fr / stmt.total_positive) if stmt and stmt.total_positive > 0 else Fraction(1)
            impact = restate.delta * scale
            db.add(models.BridgeLayer(
                run_id=run.id, receivable_id=receivable.id,
                layer=models.BR_RESTATE, amount=_dec(impact),
                currency=receivable.currency, reporting_amount=_dec(impact),
                reporting_currency=receivable.currency,
                detail_json=json.dumps(restate.meta, ensure_ascii=False),
            ))
        # 没有重述影响时不写该层（桥接器只发布真实存在的差异）

        # 5. 无法归属：应收金额 - 已核验到账（同币种原币口径）
        unalloc = recv_fr - to_fraction(verified_amount)
        if receivable.id in chain_totals:
            gap = recv_fr - chain_totals[receivable.id]
            if gap > 0:
                db.add(models.SourceChain(
                    run_id=run.id, receivable_id=receivable.id,
                    entry_kind="unallocatable", path_json="[]",
                    terminal_company_subject_id=None, terminal_statement_id=None,
                    terminal_line_id=None, path_weight=Decimal("0"),
                    amount=_dec(gap), currency=receivable.currency,
                    detail_json=json.dumps({
                        "reason": "来源链口径与应收金额的差额，无法归属",
                        "chain_total": str(_dec(chain_totals[receivable.id])),
                        "receivable_amount": str(_dec(recv_fr)),
                    }, ensure_ascii=False),
                ))
        if unalloc > 0:
            db.add(models.BridgeLayer(
                run_id=run.id, receivable_id=receivable.id,
                layer=models.BR_UNALLOC, amount=_dec(unalloc),
                currency=receivable.currency, reporting_amount=None,
                reporting_currency=receivable.currency, fx_status=None,
                detail_json=json.dumps({
                    "reason": "已声明应收中尚无外部到账证据的部分",
                    "declared": str(_dec(recv_fr)),
                    "verified": str(verified_amount),
                    "one_to_many_excluded": receivable.id in conflict_receivable_flags,
                }, ensure_ascii=False),
            ))

    # ---------- 诊断 ----------
    diagnostics = {
        "receivables": len(state["receivables"]),
        "receipts": len(state["receipts"]),
        "statements": len(state["statements"]),
        "statements_effective": len([v for v in state["views"] if v.is_effective]),
        "resolutions": len(state["resolutions"]),
        "snapshots_confirmable": len([s for s in state["snapshots"]
                                      if s.status == models.SNAP_CONFIRMABLE]),
        "pending_titles": pending_titles,
        "one_to_many_conflicts": excluded_receipt_amounts,
        "verified_total_by_currency": _verified_totals(final_matches),
    }
    run.diagnostics = json.dumps(diagnostics, ensure_ascii=False, default=str)
    await db.flush()
    return {
        "run_id": run.id,
        "status": run.status,
        "diagnostics": diagnostics,
    }


def _path_weight_from_detail(chain: dict) -> float:
    pw = chain["detail"].get("path_weight")
    return float(pw) if pw is not None else 0.0


def _latest_value_date(receipts) -> date | None:
    dates = [r.value_date for r in receipts if r.value_date is not None]
    return max(dates) if dates else None


def _value_date_for_receivable(receipts, receivable) -> date | None:
    """取与该应收同收款主体、同币种的最近到账日，用于汇率差异的到账口径。"""
    dates = [
        r.value_date for r in receipts
        if r.collecting_subject_id == receivable.collecting_subject_id
        and r.currency == receivable.currency and r.direction == "inflow"
    ]
    return max(dates) if dates else None


class _Restate:
    def __init__(self, meta, delta):
        self.meta = meta
        self.delta = delta


def _restatement_delta(index, views, company_id, period_code):
    """当同一 (公司, 期间) 同时存在 original(已审核/被替代) 与 restated 两份时，
    返回 利润构成差额（Fraction）与来源说明；否则返回 None。

    多份重述时取 id 最大（最后到达）的重述表作为新口径，保证结果可复算。
    """
    originals = [v for v in views if v.company_id == company_id
                 and v.period_code == period_code and v.staging == "original"
                 and v.status in (models.FIN_APPROVED, models.FIN_SUPERSEDED)]
    restated = [v for v in views if v.company_id == company_id
                and v.period_code == period_code and v.staging == "restated"
                and v.status == models.FIN_RESTATED]
    if not originals or not restated:
        return None
    original = sorted(originals, key=lambda s: s.id)[0]
    new = sorted(restated, key=lambda s: s.id)[-1]
    delta_positive = new.total_positive - original.total_positive
    if delta_positive == 0:
        return None
    meta = {
        "reason": "子公司利润表在审计后被重述，跨期桥接生成重述前后来源差异，不回写原审计快照",
        "original_statement_id": original.id,
        "restated_statement_id": new.id,
        "original_total": str(_dec(original.total_positive)),
        "restated_total": str(_dec(new.total_positive)),
        "delta_positive": str(_dec(delta_positive)),
        "company_subject_id": company_id,
        "period_code": period_code,
    }
    return _Restate(meta, delta_positive)


def _verified_totals(matches) -> dict:
    totals: dict[str, Decimal] = {}
    for m in matches:
        if m.verified:
            key = m.currency
            totals[key] = totals.get(key, Decimal("0")) + _dec(m.matched_amount)
    return {k: str(v) for k, v in totals.items()}


async def seal_audit_snapshot(db: AsyncSession, run: models.ReconciliationRun,
                              user: str) -> models.AuditSnapshot:
    """封存利润来源、股权版本、决议资料与匹配结果；重述不回写。"""
    frozen = await _freeze_payload(db, run)
    canonical = json.dumps(frozen, ensure_ascii=False, sort_keys=True, default=str)
    seal_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    snap = models.AuditSnapshot(
        run_id=run.id, as_of_date=run.as_of_date,
        seal_hash=seal_hash, frozen_json=canonical, created_by=user,
    )
    db.add(snap)
    run.status = models.RECON_SEALED
    await db.flush()
    return snap


async def _freeze_payload(db: AsyncSession, run: models.ReconciliationRun) -> dict:
    chains = list(await db.scalars(
        select(models.SourceChain).where(models.SourceChain.run_id == run.id)
        .order_by(models.SourceChain.id)))
    matches = list(await db.scalars(
        select(models.ReceiptMatch).where(models.ReceiptMatch.run_id == run.id)
        .order_by(models.ReceiptMatch.id)))
    bridges = list(await db.scalars(
        select(models.BridgeLayer).where(models.BridgeLayer.run_id == run.id)
        .order_by(models.BridgeLayer.id)))
    receivables = list(await db.scalars(select(models.ReceivableNotice)))
    statements = list(await db.scalars(select(models.ProfitStatement)))
    resolutions = list(await db.scalars(select(models.DividendResolution)))
    snapshots = list(await db.scalars(select(models.EquitySnapshot)))
    return {
        "as_of_date": run.as_of_date.isoformat(),
        "chains": [{
            "id": c.id, "receivable_id": c.receivable_id,
            "entry_kind": c.entry_kind, "terminal_line_id": c.terminal_line_id,
            "amount": str(c.amount), "currency": c.currency,
            "path": json.loads(c.path_json or "[]"),
            "detail": json.loads(c.detail_json or "{}"),
        } for c in chains],
        "matches": [{
            "id": m.id, "receipt_id": m.receipt_id,
            "receivable_id": m.receivable_id, "rule": m.rule,
            "status": m.status, "verified": m.verified,
            "matched_amount": str(m.matched_amount),
        } for m in matches],
        "bridges": [{
            "id": b.id, "receivable_id": b.receivable_id,
            "layer": b.layer, "amount": str(b.amount) if b.amount is not None else None,
            "currency": b.currency, "fx_status": b.fx_status,
        } for b in bridges],
        "receivables": [{
            "id": r.id, "receivable_no": r.receivable_no,
            "amount": str(r.amount), "currency": r.currency,
            "confirmed_date": r.confirmed_date.isoformat(),
            "resolution_id": r.resolution_id,
        } for r in receivables],
        "profit_statements": [{
            "id": s.id, "company_subject_id": s.company_subject_id,
            "period_code": s.period_code, "status": s.status, "staging": s.staging,
            "restates_statement_id": s.restates_statement_id,
        } for s in statements],
        "resolutions": [{
            "id": r.id, "resolution_no": r.resolution_no,
            "record_date": r.record_date.isoformat(),
            "funding_period_code": r.funding_period_code,
        } for r in resolutions],
        "equity_snapshots": [{
            "id": s.id, "company_subject_id": s.company_subject_id,
            "snapshot_date": s.snapshot_date.isoformat(), "status": s.status,
        } for s in snapshots],
    }
