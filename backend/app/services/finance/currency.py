"""汇率闸门（纯函数）。

纪律：只有 approved=True 且 rate 非空的汇率来源可用于换算；否则只发布原币金额 + 缺失汇率提示，
绝不按当日市场汇率自行换算。

"汇率差异"的定义是**换算口径差异**：同一原币金额在确认日与到账日采用不同获批汇率，
分别折算到报告币后形成的差额；不是把外币金额本身当作差额。
选择汇率时按 effective_date 取"不晚于目标日期"的最近一条获批来源，保证可复算。
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


@dataclass
class FxView:
    from_currency: str
    to_currency: str
    rate: Decimal | None
    approved: bool
    source_ref: str
    effective_date: date | None = None


def pick_rate(rates: list[FxView], from_currency: str, to_currency: str,
              as_of: date | None = None) -> FxView | None:
    if from_currency == to_currency:
        return FxView(from_currency, to_currency, Decimal("1"), True, "identity", as_of)
    approved = [
        r for r in rates
        if r.from_currency == from_currency and r.to_currency == to_currency
        and r.approved and r.rate is not None
        and (as_of is None or r.effective_date is None or r.effective_date <= as_of)
    ]
    if not approved:
        return None
    # 取 effective_date 最近的一条；同日期按 source_ref 稳定排序，保证可复算
    approved.sort(key=lambda r: (
        r.effective_date or date.min, r.source_ref))
    return approved[-1]


def convert(amount: Decimal, from_currency: str, to_currency: str,
            rates: list[FxView], as_of: date | None = None) -> dict:
    """返回 {reporting_amount, fx_status, fx_source, rate, message}。

    fx_status: applied（已按获批汇率换算）/ missing（缺获批汇率，仅发布原币）。
    """
    picked = pick_rate(rates, from_currency, to_currency, as_of)
    if picked is None:
        return {
            "reporting_amount": None, "fx_status": "missing",
            "fx_source": None, "rate": None,
            "message": (f"缺少 {from_currency}->{to_currency} 的获批汇率来源，"
                        f"仅发布原币金额 {amount} {from_currency}，不自行换算"),
        }
    if from_currency == to_currency:
        return {
            "reporting_amount": amount, "fx_status": "applied",
            "fx_source": "identity", "rate": "1",
            "message": "同币种，无需换算",
        }
    converted = (amount * picked.rate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return {
        "reporting_amount": converted, "fx_status": "applied",
        "fx_source": picked.source_ref, "rate": str(picked.rate),
        "message": f"采用获批汇率来源 {picked.source_ref}（生效 {picked.effective_date}）换算",
    }


def translation_difference(
    amount: Decimal, from_currency: str, to_currency: str,
    rates: list[FxView], recognition_date: date, value_date: date,
) -> dict:
    """同一原币金额在确认日与到账日折算到报告币的差额。

    返回 {difference, rate_at_recognition, rate_at_value, status}。
    任一日期缺获批汇率时 status=missing，difference=None（只发布原币与提示）。
    """
    at_recog = pick_rate(rates, from_currency, to_currency, recognition_date)
    at_value = pick_rate(rates, from_currency, to_currency, value_date)
    if at_recog is None or at_value is None:
        return {"difference": None, "status": "missing",
                "rate_at_recognition": None, "rate_at_value": None}
    if from_currency == to_currency:
        return {"difference": Decimal("0"), "status": "applied",
                "rate_at_recognition": "1", "rate_at_value": "1"}
    recog_amt = (amount * at_recog.rate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    value_amt = (amount * at_value.rate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return {
        "difference": value_amt - recog_amt,
        "status": "applied",
        "rate_at_recognition": str(at_recog.rate),
        "rate_at_value": str(at_value.rate),
        "reporting_at_recognition": recog_amt,
        "reporting_at_value": value_amt,
    }
