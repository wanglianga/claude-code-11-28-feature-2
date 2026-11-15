"""期间对齐器（纯函数）。

需求要求把四类日期分别建模，绝不把"同月发生"视为同一会计事实：
  1. 利润表：会计期间 [period_start, period_end]
  2. 决议：决议记录日 declaration_date 与股权登记日 record_date
  3. 应收通知：应收确认日 confirmed_date
  4. 银行回单：到账日期 value_date

对齐器只做"某日期落在哪个会计期间 / 是否早于/晚于分析日"的边界判断，
不把周期边界不同的两份资料合并成同一事实。
"""
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Period:
    code: str
    start: date
    end: date


def period_of(day: date, periods: list[Period]) -> Period | None:
    """日期落在哪个会计期间（闭区间）；重叠时返回开始最晚的那个，不做归并。"""
    hits = [p for p in periods if p.start <= day <= p.end]
    if not hits:
        return None
    hits.sort(key=lambda p: (p.start, p.end))
    return hits[-1]


def is_within(day: date, period: Period) -> bool:
    return period.start <= day <= period.end


def cross_period(receipt_value_date: date, receivable_confirmed_date: date,
                 periods: list[Period]) -> dict:
    """判断到账是否跨会计期间，并明确两边各自的会计期间。

    同一自然月但不同会计期间（如分期结算）仍判为跨期——按期间而非按月。
    """
    recv_p = period_of(receivable_confirmed_date, periods)
    value_p = period_of(receipt_value_date, periods)
    if recv_p is None and value_p is None:
        return {"cross": receipt_value_date != receivable_confirmed_date,
                "receivable_period": None, "receipt_period": None,
                "reason": "两个日期都不在任何已登记会计期间"}
    if recv_p and value_p and recv_p.code == value_p.code:
        return {"cross": False,
                "receivable_period": recv_p.code, "receipt_period": value_p.code,
                "reason": "到账与应收确认落在同一会计期间"}
    return {
        "cross": True,
        "receivable_period": recv_p.code if recv_p else None,
        "receipt_period": value_p.code if value_p else None,
        "reason": "到账与应收确认分属不同会计期间（按期间边界判定，不按同月）",
    }


def aligns_with_record_date(record_date: date, snapshot_date: date) -> bool:
    """决议引用的股权登记日是否与时点股权快照的登记日一致。"""
    return record_date == snapshot_date


def timeline_entry(kind: str, day: date | None, label: str, ref: dict) -> dict:
    return {"kind": kind, "date": day.isoformat() if day else None,
            "label": label, "ref": ref}
