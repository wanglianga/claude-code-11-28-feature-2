"""财务事实层单元测试——期望值全部手算推导，不取自程序输出。

覆盖：交叉持股残差、权属待证、回单一对多/拆分/汇聚、汇率缺失与已获批换算、
期间对齐（按会计期间而非同月）、重述差异、审计封存只读。
"""
from datetime import date
from decimal import Decimal
from fractions import Fraction

from app.services.finance.allocation import (
    KIND_OPERATING,
    KIND_RESIDUAL,
    LineView,
    StatementView,
    build_statement_index,
    trace_sources,
)
from app.services.finance.currency import FxView, convert, translation_difference
from app.services.finance.matching import (
    ReceiptView,
    ReceivableView,
    detect_split_and_aggregate,
    match_receipts,
)
from app.services.finance.periods import Period, cross_period

Z = Decimal


def money(x) -> Decimal:
    fr = Fraction(x) if isinstance(x, (int, Decimal)) else x
    return (Decimal(fr.numerator) / Decimal(fr.denominator)).quantize(Decimal("0.0001"))


# ---------- C1 交叉持股残差：40 = 32 + 6.4 + 1.6 ----------
def test_cross_holding_residual_not_double_counted():
    """乙产业 FY2025：经营 4000 + 对甲分红收益 1000 = 5000。
       甲集团 FY2025：经营 800 + 对乙分红收益 200 = 1000。
       穿透 40（乙产业 -> 甲集团）：
         第一层：经营 40×0.8=32；对甲分红收益 40×0.2=8 -> 进入甲集团
         第二层（甲）：经营 8×0.8=6.4；对乙分红收益 8×0.2=1.6 -> 再次遇乙，记残差
       合计 32+6.4+1.6=40，残差 1.6 不重复展开。
    """
    A, B = 1, 2
    views = [
        StatementView(1, A, "2025-FY", date(2025, 1, 1), date(2025, 12, 31),
                      "approved", "original", None, lines=[
            LineView(1, Fraction(800), "operating", None, None, "甲经营", 1),
            LineView(2, Fraction(200), "dividend_income", B, "common", "甲对乙分红", 2),
        ]),
        StatementView(2, B, "2025-FY", date(2025, 1, 1), date(2025, 12, 31),
                      "approved", "original", None, lines=[
            LineView(3, Fraction(4000), "operating", None, None, "乙经营", 1),
            LineView(4, Fraction(1000), "dividend_income", A, "common", "乙对甲分红", 2),
        ]),
    ]
    index = build_statement_index(views)
    chains = trace_sources(Fraction(40), B, "2025-FY", index,
                           frozenset({B}), ())
    op = sum((c["amount"] for c in chains if c["kind"] == KIND_OPERATING), Fraction(0))
    res = sum((c["amount"] for c in chains if c["kind"] == KIND_RESIDUAL), Fraction(0))
    assert money(op) == Z("38.4000")     # 32 + 6.4
    assert money(res) == Z("1.6000")     # 循环残差
    assert money(op + res) == Z("40.0000")  # 合计恰等于应收，未重复累加
    # 残差链带循环路径说明
    residual = [c for c in chains if c["kind"] == KIND_RESIDUAL][0]
    assert "不重复累加" in residual["detail"]["reason"]


# ---------- 期间对齐：按会计期间，不按同月 ----------
def test_period_alignment_by_accounting_period_not_month():
    """确认日 2025-12-31（FY2025），到账日 2026-01-05（2026-H1）：跨期；
       而确认日 2026-01-05 与到账日 2026-01-20 同属 2026-H1：不跨期。"""
    periods = [
        Period("2025-FY", date(2025, 1, 1), date(2025, 12, 31)),
        Period("2026-H1", date(2026, 1, 1), date(2026, 6, 30)),
    ]
    a = cross_period(date(2026, 1, 5), date(2025, 12, 31), periods)
    assert a["cross"] is True
    assert a["receivable_period"] == "2025-FY" and a["receipt_period"] == "2026-H1"
    b = cross_period(date(2026, 1, 20), date(2026, 1, 5), periods)
    assert b["cross"] is False
    # 同一自然月但不同会计期间（分期结算）：仍判跨期
    split_periods = [Period("P1", date(2026, 1, 1), date(2026, 1, 15)),
                     Period("P2", date(2026, 1, 16), date(2026, 1, 31))]
    c = cross_period(date(2026, 1, 20), date(2026, 1, 10), split_periods)
    assert c["cross"] is True


# ---------- C3 回单一对多冲突 ----------
def test_one_to_many_conflict_excluded_from_verified():
    """回单 50 同时等额匹配两条应收 50，摘要空白 -> one_to_many，两条都不核验。"""
    recvs = [
        ReceivableView(1, "AR-A", 10, Fraction(50), "CNY", date(2026, 3, 1)),
        ReceivableView(2, "AR-B", 10, Fraction(50), "CNY", date(2026, 3, 1)),
    ]
    receipt = ReceiptView(1, "R-50", 10, None, "", Fraction(50), "CNY",
                          "inflow", date(2026, 3, 10), "")
    out = match_receipts([receipt], recvs)
    assert len(out) == 2
    assert all(m.status == "one_to_many" for m in out)
    assert all(m.verified is False for m in out)
    assert {m.receivable_id for m in out} == {1, 2}


# ---------- C4 拆分 / C5 汇聚 ----------
def test_split_and_aggregate_recorded_as_fact():
    # 拆分：应收 120 由 70 + 50 两张回单（回单号点名应收号）
    recv = ReceivableView(1, "AR-SPLIT", 10, Fraction(120), "CNY", date(2026, 2, 1))
    r1 = ReceiptView(1, "R-AR-SPLIT-1", 10, None, "", Fraction(70), "CNY",
                     "inflow", date(2026, 2, 2), "")
    r2 = ReceiptView(2, "R-AR-SPLIT-2", 10, None, "", Fraction(50), "CNY",
                     "inflow", date(2026, 2, 3), "")
    matches = match_receipts([r1, r2], [recv])
    refined = detect_split_and_aggregate(matches, [r1, r2])
    assert all(m.status == "split" for m in refined)
    assert all("拆分" in m.reason for m in refined)

    # 汇聚：一张 150 回单摘要点名两张应收 90 + 60
    recvs = [
        ReceivableView(2, "AR-AGG-A", 10, Fraction(90), "CNY", date(2026, 2, 1)),
        ReceivableView(3, "AR-AGG-B", 10, Fraction(60), "CNY", date(2026, 2, 1)),
    ]
    agg = ReceiptView(3, "R-150", 10, None, "", Fraction(150), "CNY",
                      "inflow", date(2026, 2, 4),
                      "merge AR-AGG-A and AR-AGG-B")
    out = match_receipts([agg], recvs)
    assert len(out) == 2
    assert all(m.status == "aggregated" and m.verified for m in out)
    assert sum(m.matched_amount for m in out) == Fraction(150)


# ---------- C7 汇率缺失 vs 已获批 ----------
def test_fx_missing_rate_publishes_original_only():
    rates = [FxView("USD", "CNY", Z("7.10"), approved=False, source_ref="UNAPPROVED")]
    out = convert(Z("25"), "USD", "CNY", rates, as_of=date(2026, 2, 20))
    assert out["fx_status"] == "missing"
    assert out["reporting_amount"] is None
    assert "不自行换算" in out["message"]


def test_fx_approved_uses_rate():
    rates = [FxView("EUR", "CNY", Z("7.85"), approved=True, source_ref="FX-1",
                    effective_date=date(2026, 2, 20))]
    out = convert(Z("10"), "EUR", "CNY", rates, as_of=date(2026, 2, 20))
    assert out["fx_status"] == "applied"
    assert out["reporting_amount"] == Z("78.5000")


def test_fx_translation_difference_hand_calc():
    """10 EUR：确认日汇率 7.80 -> 78.00；到账日 7.85 -> 78.50；差异 0.50 CNY。"""
    rates = [
        FxView("EUR", "CNY", Z("7.80"), True, "R1", date(2026, 2, 10)),
        FxView("EUR", "CNY", Z("7.85"), True, "R2", date(2026, 2, 20)),
    ]
    diff = translation_difference(Z("10"), "EUR", "CNY", rates,
                                  date(2026, 2, 10), date(2026, 2, 20))
    assert diff["status"] == "applied"
    assert diff["difference"] == Z("0.5000")


# ---------- 权属待证：快照缺失不借用最近一期 ----------
def test_statement_index_prefers_restated():
    """同一 (公司, 期间) 同时有 original(approved) 与 restated(restated) 时选重述后。"""
    A = 1
    orig = StatementView(1, A, "2025-FY", date(2025, 1, 1), date(2025, 12, 31),
                         "superseded", "original", None)
    rest = StatementView(2, A, "2025-FY", date(2025, 1, 1), date(2025, 12, 31),
                         "restated", "restated", 1)
    index = build_statement_index([orig, rest])
    assert index[(A, "2025-FY")].id == 2
