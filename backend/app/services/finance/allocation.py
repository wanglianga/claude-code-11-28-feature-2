"""来源分配编译器（纯函数，便于手算核对）。

目标：把一笔上层公司的应收金额，沿"决议引用的股权快照与股份类别"确认权属，
再沿利润表的利润构成向下穿透到经营企业的利润表行。

纪律：
- 权属只认记录日（record_date）当日可确认的股权快照；缺失即"权属待证"，绝不借用最近一期。
- 交叉持股：沿路径再次遇到已访问公司时，该部分记为 cycle_residual（残差），
  不再向下递归，也不并入经营来源；因此循环部分不会被重复累加。
- 全程使用 Fraction 有理数，权重重乘与残差分摊可精确复算，最后才转换为 Decimal 落库。
- 每条链都保留完整路径（利润表行 -> 付款公司 -> ...），差异可逐链追溯。
"""
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction

from ...models import FIN_APPROVED, FIN_RESTATED
from ...models import ProfitLine, ProfitStatement

# 链的类别
KIND_OPERATING = "operating_line"
KIND_RESIDUAL = "cycle_residual"
KIND_TITLE_PENDING = "title_pending"
KIND_UNALLOCATABLE = "unallocatable"

_ZERO = Fraction(0)


def to_fraction(value) -> Fraction:
    """Decimal / int / str -> 精确 Fraction；避免二进制浮点误差。"""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, Decimal):
        return Fraction(value)
    return Fraction(Decimal(str(value)))


@dataclass
class LineView:
    id: int
    amount: Fraction
    category: str
    payer_company_id: int | None
    share_class: str | None
    label: str
    line_no: int


@dataclass
class StatementView:
    id: int
    company_id: int
    period_code: str
    period_start: object
    period_end: object
    status: str
    staging: str
    restates_statement_id: int | None
    lines: list[LineView] = field(default_factory=list)

    @property
    def is_effective(self) -> bool:
        return self.status == FIN_APPROVED or self.status == FIN_RESTATED

    @property
    def total_positive(self) -> Fraction:
        return sum((ln.amount for ln in self.lines if ln.amount > 0), _ZERO)


def build_statement_index(statements: list[StatementView]) -> dict:
    """(company_id, period_code) -> 有效利润表。

    优先级：重述后（restated）> 原始已审核（approved）；superseded/draft 不参与。
    同优先级取 id 较大者（后到为准），保证同一时点只有一份有效口径。
    """
    chosen: dict = {}
    for st in statements:
        if not st.is_effective:
            continue
        key = (st.company_id, st.period_code)
        cur = chosen.get(key)
        rank = 1 if st.staging == "restated" else 0
        if cur is None:
            chosen[key] = st
            continue
        cur_rank = 1 if cur.staging == "restated" else 0
        if (rank, st.id) > (cur_rank, cur.id):
            chosen[key] = st
    return chosen


def select_funding_statement(
    index: dict, company_id: int, period_code: str, before_period_end=None
) -> StatementView | None:
    """选取付款公司的资金利润表：优先同一会计期间；否则取期间结束日不晚于父期的最近一期。

    明确按会计期间（而非自然月）对齐：同月发生不等于同一会计事实。
    """
    direct = index.get((company_id, period_code))
    if direct is not None:
        return direct
    candidates = [
        st for (cid, _p), st in index.items()
        if cid == company_id and (
            before_period_end is None or st.period_end <= before_period_end)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda s: (s.period_end, s.id))
    return candidates[-1]


def trace_sources(
    amount: Fraction,
    company_id: int,
    period_code: str,
    index: dict,
    visited: frozenset[int],
    path: tuple,
    depth: int = 0,
) -> list[dict]:
    """把金额按利润构成向下穿透。返回链列表；残差与经营来源分开。"""
    if amount <= _ZERO:
        return []
    if depth > 32:  # 防御性：正常由 visited 截断，此处仅为不收敛数据的兜底
        return [{
            "kind": KIND_UNALLOCATABLE,
            "amount": amount,
            "path": list(path),
            "detail": {"reason": "穿透深度超限，停止递归"},
        }]

    stmt = select_funding_statement(index, company_id, period_code)
    if stmt is None or stmt.total_positive <= _ZERO:
        return [{
            "kind": KIND_UNALLOCATABLE,
            "amount": amount,
            "path": list(path),
            "detail": {
                "reason": "缺少可用的利润表或利润构成全为非正数",
                "company_subject_id": company_id,
                "period_code": period_code,
            },
        }]

    total = stmt.total_positive
    chains: list[dict] = []
    for line in stmt.lines:
        if line.amount <= _ZERO:
            continue
        weight = line.amount / total
        part = amount * weight
        step = {
            "company_subject_id": company_id,
            "statement_id": stmt.id,
            "period_code": stmt.period_code,
            "line_id": line.id,
            "line_no": line.line_no,
            "label": line.label,
            "line_amount": str(Decimal(line.amount.numerator) / Decimal(line.amount.denominator)),
            "weight": _frac_str(weight),
        }
        next_path = path + (step,)
        if line.category == "dividend_income" and line.payer_company_id is not None:
            payer = line.payer_company_id
            if payer in visited:
                # 交叉持股：该部分保留为残差，不再递归，避免循环重复累加
                chains.append({
                    "kind": KIND_RESIDUAL,
                    "amount": part,
                    "path": list(next_path),
                    "detail": {
                        "reason": "交叉持股形成循环路径，残差单列，不重复累加",
                        "payer_company_subject_id": payer,
                        "cycle_path": _cycle_companies(visited, payer),
                        "path_weight": _frac_str(_path_weight(next_path)),
                    },
                })
            else:
                child = trace_sources(
                    part, payer, stmt.period_code, index,
                    visited | {payer}, next_path, depth + 1,
                )
                chains.extend(child)
        else:
            chains.append({
                "kind": KIND_OPERATING,
                "amount": part,
                "path": list(next_path),
                "detail": {
                    "company_subject_id": company_id,
                    "statement_id": stmt.id,
                    "line_id": line.id,
                    "line_category": line.category,
                    "line_label": line.label,
                    "path_weight": _frac_str(_path_weight(next_path)),
                },
            })
    return chains


def _path_weight(path: tuple) -> Fraction:
    w = Fraction(1)
    for step in path:
        w *= Fraction(step["weight"])
    return w


def _cycle_companies(visited: frozenset[int], payer: int) -> list[int]:
    return sorted(set(visited) | {payer})


def _frac_str(fr: Fraction) -> str:
    return f"{Decimal(fr.numerator) / Decimal(fr.denominator):.10f}"


def title_pending_chain(amount: Fraction, reason: str, detail: dict | None = None) -> dict:
    return {
        "kind": KIND_TITLE_PENDING,
        "amount": amount,
        "path": [],
        "detail": {"reason": reason, **(detail or {})},
    }


def chain_to_money(fr: Fraction) -> Decimal:
    """Fraction -> Decimal（4 位，金额列精度）。"""
    return (Decimal(fr.numerator) / Decimal(fr.denominator)).quantize(Decimal("0.0001"))


def load_statement_views(statements: list[ProfitStatement],
                         lines_by_statement: dict[int, list[ProfitLine]]) -> list[StatementView]:
    views: list[StatementView] = []
    for st in statements:
        lines = [
            LineView(
                id=ln.id, amount=to_fraction(ln.amount), category=ln.line_category,
                payer_company_id=ln.payer_company_subject_id,
                share_class=ln.share_class, label=ln.label, line_no=ln.line_no,
            )
            for ln in lines_by_statement.get(st.id, [])
        ]
        views.append(StatementView(
            id=st.id, company_id=st.company_subject_id, period_code=st.period_code,
            period_start=st.period_start, period_end=st.period_end,
            status=st.status, staging=st.staging,
            restates_statement_id=st.restates_statement_id, lines=lines,
        ))
    return views


def effective_statement_id(index: dict, company_id: int, period_code: str) -> int | None:
    st = select_funding_statement(index, company_id, period_code)
    return st.id if st else None
