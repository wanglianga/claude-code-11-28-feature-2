"""虚构财务事实验收样本（幂等）。

覆盖验收要求：
  C1 交叉持股残差：乙产业利润含对甲集团分红收益，甲集团利润又含对乙产业分红收益；
     40 万元应收穿透为 乙经营 32 + 甲经营 6.4 + 循环残差 1.6，残差不重复累加。
  C2 跨期到账：应收确认在 FY2025，回单到账在 2026-H1，按会计期间而非同月判定为跨期。
  C3 回单一对多冲突：同一 50 万元回单等额匹配两条应收且摘要无法区分，保持候选并排除核验。
  C4 回单拆分：一张 120 万元应收由 70 + 50 两张回单拆分到账，理由记为事实。
  C5 回单汇聚：一张 150 万元回单汇聚 90 + 60 两张应收，理由记为事实。
  C6 利润重述：辛股份 FY2025 利润由 350 重述为 300，桥接生成 -50 重述影响，不回写审计快照。
  C7 汇率缺失：USD 应收无获批汇率，仅发布原币 25 USD 并提示缺失；EUR 有获批汇率 7.85。
  C8 权属待证：甲集团决议记录日 2025-11-30 无可确认股权快照，应收列为权属待证。

所有企业、金额、文号均为虚构，仅用于演示，不执行任何分红批准/付款/资金调度。
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models
from .finance_ingest import (
    approve_profit_statement,
    approve_resolution,
    import_fx_rate,
    import_profit_statement,
    import_receipt,
    import_receivable,
    import_resolution,
    import_snapshot,
)
from .finance_schemas import (
    DividendResolutionIn,
    EquitySnapshotIn,
    FxRateIn,
    ProfitLineIn,
    ProfitStatementIn,
    ReceiptIn,
    ReceivableIn,
    ResolutionLineIn,
    SnapshotHoldingIn,
)

D = date
Z = Decimal


async def _subject_by_code(db: AsyncSession, code: str) -> models.Subject:
    s = await db.scalar(select(models.Subject).where(models.Subject.unified_code == code))
    assert s is not None, f"主体代码不存在：{code}"
    return s


async def _subject_by_name(db: AsyncSession, name: str) -> models.Subject:
    s = await db.scalar(
        select(models.Subject).where(models.Subject.name == name)
        .order_by(models.Subject.id))
    assert s is not None, f"主体名称不存在：{name}"
    return s


async def _profit(db, *, source_ref, company_id, period_code, start, end,
                  staging, restates_ref, lines) -> int:
    body = ProfitStatementIn(
        source_ref=source_ref, company_subject_id=company_id,
        period_code=period_code, period_start=start, period_end=end,
        staging=staging, restates_source_ref=restates_ref,
        provenance="虚构利润表（验收样本）",
        lines=[ProfitLineIn(
            line_no=i + 1, line_category=cat, label=label, amount=Z(amount),
            payer_company_subject_id=payer, share_class=cls)
            for i, (cat, label, amount, payer, cls) in enumerate(lines)],
    )
    out = await import_profit_statement(db, body, "admin")
    return out["statement_id"]


async def _snapshot(db, *, company_id, snap_date, source_ref, holdings) -> int:
    body = EquitySnapshotIn(
        company_subject_id=company_id, snapshot_date=snap_date,
        source_ref=source_ref, provenance="虚构股权快照（验收样本）",
        holdings=[SnapshotHoldingIn(
            holder_subject_id=hid, holder_label=label, share_class=cls,
            shares=Z(shares), class_total_shares=Z(total))
            for (hid, label, cls, shares, total) in holdings],
    )
    return (await import_snapshot(db, body, "admin"))["snapshot_id"]


async def _resolution(db, *, company_id, no, declaration, record, cls, total,
                      currency, funding, lines) -> models.DividendResolution:
    body = DividendResolutionIn(
        company_subject_id=company_id, resolution_no=no,
        declaration_date=declaration, record_date=record, share_class=cls,
        total_declared_amount=Z(total), currency=currency,
        funding_period_code=funding,
        provenance="虚构董事会分红决议（验收样本）",
        lines=[ResolutionLineIn(
            line_no=i + 1, holder_subject_id=hid, holder_label=label,
            share_class=cls, declared_amount=Z(amount), currency=currency)
            for i, (hid, label, amount) in enumerate(lines)],
    )
    out = await import_resolution(db, body, "admin")
    res = await db.get(models.DividendResolution, out["resolution_id"])
    return res


async def _line_id(db, resolution_id: int, line_no: int) -> int:
    return await db.scalar(
        select(models.ResolutionLine.id).where(
            models.ResolutionLine.resolution_id == resolution_id,
            models.ResolutionLine.line_no == line_no))


async def _receivable(db, *, issuer, collecting, no, amount, currency, confirmed,
                      resolution_id, resolution_line_id, source_ref) -> int:
    body = ReceivableIn(
        issuer_subject_id=issuer, collecting_subject_id=collecting,
        receivable_no=no, amount=Z(amount), currency=currency,
        confirmed_date=confirmed, resolution_id=resolution_id,
        resolution_line_id=resolution_line_id, source_ref=source_ref,
        provenance="虚构应收通知（验收样本）",
    )
    return (await import_receivable(db, body, "admin"))["receivable_id"]


async def _receipt(db, *, no, collecting, payer, payer_label, amount, currency,
                   value_date, memo, source_ref) -> int:
    body = ReceiptIn(
        receipt_no=no, collecting_subject_id=collecting,
        payer_subject_id=payer, payer_label=payer_label,
        amount=Z(amount), currency=currency, value_date=value_date,
        memo=memo, source_ref=source_ref,
        provenance="虚构银行回单（验收样本）",
    )
    return (await import_receipt(db, body, "admin"))["receipt_id"]


async def seed_finance(db: AsyncSession, user: str = "admin") -> dict:
    """幂等播种财务事实样本；返回摘要。"""
    existing = await db.scalar(select(models.ProfitStatement.id).limit(1))
    summary: dict = {"skipped": False}
    if existing:
        summary["skipped"] = True
        summary["message"] = "财务事实样本已存在，幂等跳过"
        return summary

    A = await _subject_by_code(db, "91310000MA1FL0001A")   # 甲集团
    B = await _subject_by_code(db, "91310000MA1FL0002B")   # 乙产业
    F = await _subject_by_code(db, "91310000MA1FL0006F")   # 戊科技
    H = await _subject_by_code(db, "91310000MA1FL0008H")   # 庚物流
    K = await _subject_by_code(db, "91310000MA1FL0011K")   # 辛股份
    I1 = await _subject_by_name(db, "晨星股权投资基金（虚构）")
    I3 = await _subject_by_name(db, "同裕产业基金（虚构）")
    P1 = await _subject_by_name(db, "张东明")
    P2 = await _subject_by_name(db, "李若兰")
    P4 = await _subject_by_name(db, "赵启年")
    I2 = await _subject_by_name(db, "远海资管（虚构）")

    # ---------- 利润表 ----------
    # 甲集团 FY2025：经营 800 + 对乙分红收益 200 = 1000
    stmt_a = await _profit(db, source_ref="PS-A-FY2025", company_id=A.id,
                           period_code="2025-FY", start=D(2025, 1, 1), end=D(2025, 12, 31),
                           staging="original", restates_ref=None, lines=[
        ("operating", "甲集团主营业务利润", "800", None, None),
        ("dividend_income", "甲集团对乙产业分红收益", "200", B.id, "common"),
    ])
    # 乙产业 FY2025：经营 4000 + 对甲分红收益 1000 = 5000
    stmt_b = await _profit(db, source_ref="PS-B-FY2025", company_id=B.id,
                           period_code="2025-FY", start=D(2025, 1, 1), end=D(2025, 12, 31),
                           staging="original", restates_ref=None, lines=[
        ("operating", "乙产业主营业务利润", "4000", None, None),
        ("dividend_income", "乙产业对甲集团分红收益", "1000", A.id, "common"),
    ])
    stmt_f = await _profit(db, source_ref="PS-F-FY2025", company_id=F.id,
                           period_code="2025-FY", start=D(2025, 1, 1), end=D(2025, 12, 31),
                           staging="original", restates_ref=None, lines=[
        ("operating", "戊科技主营业务利润", "700", None, None),
    ])
    stmt_f_h1 = await _profit(db, source_ref="PS-F-2026H1", company_id=F.id,
                              period_code="2026-H1", start=D(2026, 1, 1), end=D(2026, 6, 30),
                              staging="original", restates_ref=None, lines=[
        ("operating", "戊科技 2026 上半年经营利润", "500", None, None),
    ])
    stmt_h = await _profit(db, source_ref="PS-H-FY2025", company_id=H.id,
                           period_code="2025-FY", start=D(2025, 1, 1), end=D(2025, 12, 31),
                           staging="original", restates_ref=None, lines=[
        ("operating", "庚物流主营业务利润", "600", None, None),
    ])
    stmt_k_orig = await _profit(db, source_ref="PS-K-FY2025-ORIG", company_id=K.id,
                                period_code="2025-FY", start=D(2025, 1, 1), end=D(2025, 12, 31),
                                staging="original", restates_ref=None, lines=[
        ("operating", "辛股份主营业务利润", "300", None, None),
        ("other", "辛股份其他收益", "50", None, None),
    ])
    stmt_k_rest = await _profit(db, source_ref="PS-K-FY2025-REST", company_id=K.id,
                                period_code="2025-FY", start=D(2025, 1, 1), end=D(2025, 12, 31),
                                staging="restated", restates_ref="PS-K-FY2025-ORIG", lines=[
        ("operating", "辛股份主营业务利润（重述后）", "250", None, None),
        ("other", "辛股份其他收益（重述后）", "50", None, None),
    ])

    for sid in (stmt_a, stmt_b, stmt_f, stmt_f_h1, stmt_h, stmt_k_orig):
        obj = await db.get(models.ProfitStatement, sid)
        await approve_profit_statement(db, obj, "reviewer")
    # 重述表审批：旧表自动置 superseded（原值保留）
    obj = await db.get(models.ProfitStatement, stmt_k_rest)
    await approve_profit_statement(db, obj, "reviewer")

    # ---------- 时点股权快照 ----------
    await _snapshot(db, company_id=B.id, snap_date=D(2026, 1, 15), source_ref="SNAP-B-20260115",
                    holdings=[(A.id, "甲集团股份有限公司（虚构）", "common", "200", "1000"),
                              (I1.id, "晨星股权投资基金（虚构）", "common", "800", "1000")])
    await _snapshot(db, company_id=A.id, snap_date=D(2026, 1, 15), source_ref="SNAP-A-20260115",
                    holdings=[(P1.id, "张东明", "common", "600", "1000"),
                              (B.id, "乙产业有限公司（虚构）", "common", "400", "1000")])
    await _snapshot(db, company_id=K.id, snap_date=D(2026, 1, 20), source_ref="SNAP-K-20260120",
                    holdings=[(P4.id, "赵启年", "common", "1200", "2000"),
                              (I3.id, "同裕产业基金（虚构）", "common", "800", "2000")])
    G = await _subject_by_code(db, "91310000MA1FL0007G")
    await _snapshot(db, company_id=H.id, snap_date=D(2026, 2, 15), source_ref="SNAP-H-20260215",
                    holdings=[(G.id, "己智造有限公司（虚构）", "common", "600", "1000"),
                              (P1.id, "张东明", "common", "400", "1000")])
    await _snapshot(db, company_id=F.id, snap_date=D(2025, 12, 31), source_ref="SNAP-F-20251231",
                    holdings=[(P2.id, "李若兰", "common", "4500", "6000"),
                              (I2.id, "远海资管（虚构）", "common", "1500", "6000")])
    # 注意：甲集团记录日 2025-11-30 故意没有可确认快照，用于 C8 权属待证。

    # ---------- 分红决议 ----------
    res_b = await _resolution(db, company_id=B.id, no="RES-B-2026-01",
                              declaration=D(2026, 1, 10), record=D(2026, 1, 15),
                              cls="common", total="200", currency="CNY", funding="2025-FY",
                              lines=[(A.id, "甲集团股份有限公司（虚构）", "40"),
                                     (I1.id, "晨星股权投资基金（虚构）", "160")])
    res_a = await _resolution(db, company_id=A.id, no="RES-A-2025-11",
                              declaration=D(2025, 11, 20), record=D(2025, 11, 30),
                              cls="common", total="100", currency="CNY", funding="2025-FY",
                              lines=[(B.id, "乙产业有限公司（虚构）", "40"),
                                     (P1.id, "张东明", "60")])
    res_b_big = await _resolution(db, company_id=B.id, no="RES-B-2026-02",
                                  declaration=D(2026, 2, 1), record=D(2026, 1, 15),
                                  cls="common", total="2000", currency="CNY", funding="2025-FY",
                                  lines=[(A.id, "甲集团股份有限公司（虚构）", "400"),
                                         (I1.id, "晨星股权投资基金（虚构）", "1600")])
    res_b_usd = await _resolution(db, company_id=B.id, no="RES-B-2026-01-USD",
                                  declaration=D(2026, 1, 12), record=D(2026, 1, 15),
                                  cls="common", total="200", currency="USD", funding="2025-FY",
                                  lines=[(A.id, "甲集团股份有限公司（虚构）", "25"),
                                         (I1.id, "晨星股权投资基金（虚构）", "175")])
    res_b_eur = await _resolution(db, company_id=B.id, no="RES-B-2026-01-EUR",
                                  declaration=D(2026, 1, 12), record=D(2026, 1, 15),
                                  cls="common", total="100", currency="EUR", funding="2025-FY",
                                  lines=[(A.id, "甲集团股份有限公司（虚构）", "10"),
                                         (I1.id, "晨星股权投资基金（虚构）", "90")])
    res_h = await _resolution(db, company_id=H.id, no="RES-H-2026-02",
                              declaration=D(2026, 2, 10), record=D(2026, 2, 15),
                              cls="common", total="100", currency="CNY", funding="2025-FY",
                              lines=[(G.id, "己智造有限公司（虚构）", "100")])
    res_k = await _resolution(db, company_id=K.id, no="RES-K-2026-01",
                              declaration=D(2026, 1, 15), record=D(2026, 1, 20),
                              cls="common", total="100", currency="CNY", funding="2025-FY",
                              lines=[(P4.id, "赵启年", "60"), (I3.id, "同裕产业基金（虚构）", "40")])
    res_f = await _resolution(db, company_id=F.id, no="RES-F-2025-12",
                              declaration=D(2025, 12, 20), record=D(2025, 12, 31),
                              cls="common", total="100", currency="CNY", funding="2025-FY",
                              lines=[(P2.id, "李若兰", "75"), (I2.id, "远海资管（虚构）", "25")])

    for res in (res_b, res_a, res_b_big, res_b_usd, res_b_eur, res_h, res_k, res_f):
        await approve_resolution(db, res, "reviewer")

    # ---------- 应收通知 ----------
    # C1 交叉持股残差：乙产业 -> 甲集团 40
    await _receivable(db, issuer=B.id, collecting=A.id, no="AR-B-2026-01",
                      amount="40", currency="CNY", confirmed=D(2026, 2, 10),
                      resolution_id=res_b.id,
                      resolution_line_id=await _line_id(db, res_b.id, 1),
                      source_ref="ARN-B-2026-01")
    # C8 权属待证：甲集团 -> 乙产业 40（记录日 2025-11-30 无快照）
    await _receivable(db, issuer=A.id, collecting=B.id, no="AR-A-2025-11",
                      amount="40", currency="CNY", confirmed=D(2025, 12, 5),
                      resolution_id=res_a.id,
                      resolution_line_id=await _line_id(db, res_a.id, 1),
                      source_ref="ARN-A-2025-11")
    # C4 拆分：120 由 70 + 50 两张回单拆分
    await _receivable(db, issuer=B.id, collecting=A.id, no="AR-B-SPLIT-1201",
                      amount="120", currency="CNY", confirmed=D(2026, 2, 10),
                      resolution_id=res_b_big.id,
                      resolution_line_id=await _line_id(db, res_b_big.id, 1),
                      source_ref="ARN-B-SPLIT-1201")
    # C5 汇聚：90 与 60 由一张 150 回单汇聚
    await _receivable(db, issuer=B.id, collecting=A.id, no="AR-B-AGG-0901",
                      amount="90", currency="CNY", confirmed=D(2026, 2, 10),
                      resolution_id=res_b_big.id,
                      resolution_line_id=await _line_id(db, res_b_big.id, 1),
                      source_ref="ARN-B-AGG-0901")
    await _receivable(db, issuer=B.id, collecting=A.id, no="AR-B-AGG-0601",
                      amount="60", currency="CNY", confirmed=D(2026, 2, 10),
                      resolution_id=res_b_big.id,
                      resolution_line_id=await _line_id(db, res_b_big.id, 1),
                      source_ref="ARN-B-AGG-0601")
    # C3 一对多冲突：庚物流 -> 己智造 两张 50，摘要无法区分
    await _receivable(db, issuer=H.id, collecting=G.id, no="AR-H-2026-01A",
                      amount="50", currency="CNY", confirmed=D(2026, 3, 1),
                      resolution_id=res_h.id,
                      resolution_line_id=await _line_id(db, res_h.id, 1),
                      source_ref="ARN-H-2026-01A")
    await _receivable(db, issuer=H.id, collecting=G.id, no="AR-H-2026-01B",
                      amount="50", currency="CNY", confirmed=D(2026, 3, 1),
                      resolution_id=res_h.id,
                      resolution_line_id=await _line_id(db, res_h.id, 1),
                      source_ref="ARN-H-2026-01B")
    # C6 重述：辛股份 -> 赵启年 60
    await _receivable(db, issuer=K.id, collecting=P4.id, no="AR-K-2026-01",
                      amount="60", currency="CNY", confirmed=D(2026, 2, 20),
                      resolution_id=res_k.id,
                      resolution_line_id=await _line_id(db, res_k.id, 1),
                      source_ref="ARN-K-2026-01")
    # C2 跨期到账：戊科技 -> 远海 25，确认 FY2025，到账 2026-H1
    await _receivable(db, issuer=F.id, collecting=I2.id, no="AR-F-2025-12-001",
                      amount="25", currency="CNY", confirmed=D(2025, 12, 31),
                      resolution_id=res_f.id,
                      resolution_line_id=await _line_id(db, res_f.id, 2),
                      source_ref="ARN-F-2025-12-001")
    # C7 汇率缺失：USD 25
    await _receivable(db, issuer=B.id, collecting=A.id, no="AR-B-USD-001",
                      amount="25", currency="USD", confirmed=D(2026, 2, 10),
                      resolution_id=res_b_usd.id,
                      resolution_line_id=await _line_id(db, res_b_usd.id, 1),
                      source_ref="ARN-B-USD-001")
    # EUR 有获批汇率
    await _receivable(db, issuer=B.id, collecting=A.id, no="AR-B-EUR-001",
                      amount="10", currency="EUR", confirmed=D(2026, 2, 10),
                      resolution_id=res_b_eur.id,
                      resolution_line_id=await _line_id(db, res_b_eur.id, 1),
                      source_ref="ARN-B-EUR-001")

    # ---------- 银行回单 ----------
    await _receipt(db, no="RCPT-B-01", collecting=A.id, payer=B.id,
                   payer_label="乙产业有限公司（虚构）", amount="40", currency="CNY",
                   value_date=D(2026, 2, 20), memo="乙产业2025年度分红到账",
                   source_ref="BR-B-01")
    # C4 拆分：两张回单回单号均含应收号
    await _receipt(db, no="RCPT-SPLIT-1-AR-B-SPLIT-1201", collecting=A.id, payer=B.id,
                   payer_label="乙产业有限公司（虚构）", amount="70", currency="CNY",
                   value_date=D(2026, 2, 18), memo="拆分第一笔",
                   source_ref="BR-SPLIT-1")
    await _receipt(db, no="RCPT-SPLIT-2-AR-B-SPLIT-1201", collecting=A.id, payer=B.id,
                   payer_label="乙产业有限公司（虚构）", amount="50", currency="CNY",
                   value_date=D(2026, 2, 25), memo="拆分第二笔",
                   source_ref="BR-SPLIT-2")
    # C5 汇聚：一张 150 回单，摘要点名两张应收
    await _receipt(db, no="RCPT-AGG-150", collecting=A.id, payer=B.id,
                   payer_label="乙产业有限公司（虚构）", amount="150", currency="CNY",
                   value_date=D(2026, 2, 26),
                   memo="汇聚到账：AR-B-AGG-0901 与 AR-B-AGG-0601 合并汇入",
                   source_ref="BR-AGG-150")
    # C3 一对多：50 回单，摘要空白
    await _receipt(db, no="RCPT-H-50", collecting=G.id, payer=H.id,
                   payer_label="庚物流有限公司（虚构）", amount="50", currency="CNY",
                   value_date=D(2026, 3, 10), memo="",
                   source_ref="BR-H-50")
    # C2 跨期：25 回单，回单号点名应收
    await _receipt(db, no="RCPT-XP-AR-F-2025-12-001", collecting=I2.id, payer=F.id,
                   payer_label="戊科技股份有限公司（虚构）", amount="25", currency="CNY",
                   value_date=D(2026, 1, 5), memo="跨期到账",
                   source_ref="BR-F-XP")
    # C7 USD 回单
    await _receipt(db, no="RCPT-USD-001", collecting=A.id, payer=B.id,
                   payer_label="乙产业有限公司（虚构）", amount="25", currency="USD",
                   value_date=D(2026, 2, 20), memo="USD 分红到账",
                   source_ref="BR-USD-001")
    # EUR 回单
    await _receipt(db, no="RCPT-EUR-001", collecting=A.id, payer=B.id,
                   payer_label="乙产业有限公司（虚构）", amount="10", currency="EUR",
                   value_date=D(2026, 2, 20), memo="EUR 分红到账",
                   source_ref="BR-EUR-001")

    # ---------- 汇率 ----------
    # USD->CNY 未获批（rate 有值但 approved=False），系统不得使用
    await import_fx_rate(db, FxRateIn(
        from_currency="USD", to_currency="CNY", rate=Z("7.10"), approved=False,
        source_ref="FX-USD-CNY-UNAPPROVED", provenance="未获批的参考报价（不得用于换算）",
        effective_date=D(2026, 2, 20)), "admin")
    # EUR->CNY 两条已获批汇率：确认日 7.80、到账日 7.85，
    # 换算差异 = 10 × (7.85 − 7.80) = 0.50 CNY（可手算复核）
    await import_fx_rate(db, FxRateIn(
        from_currency="EUR", to_currency="CNY", rate=Z("7.80"), approved=True,
        source_ref="FX-EUR-CNY-20260210", provenance="已获批的虚构汇率来源（确认日口径）",
        effective_date=D(2026, 2, 10)), "admin")
    await import_fx_rate(db, FxRateIn(
        from_currency="EUR", to_currency="CNY", rate=Z("7.85"), approved=True,
        source_ref="FX-EUR-CNY-20260220", provenance="已获批的虚构汇率来源（到账日口径）",
        effective_date=D(2026, 2, 20)), "admin")

    await db.commit()
    summary.update({
        "profit_statements": 7, "equity_snapshots": 5,
        "resolutions": 8, "receivables": 11, "receipts": 8,
        "fx_rates": 3, "recon_as_of": "2026-06-30",
    })
    return summary
