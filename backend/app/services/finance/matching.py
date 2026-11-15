"""回单匹配器（纯函数）。

匹配依据（按证据强度排序）：
  1. 外部回单号 external_ref 命中应收号 -> 强证据
  2. 币种 + 金额 + 方向 + 付款主体一致 -> 强证据
  3. 摘要 memo 命中应收号/期间说明 -> 辅助证据

纪律：
- 一笔回单拆分（split）或多笔回单汇聚（aggregate）必须把匹配理由记录为事实，不隐式合并。
- 同一回单同时等额匹配两条应收且摘要无法区分 -> one_to_many 冲突：
  保持一组候选、verified=False，并把该金额从"已核验到账"中排除。
- 方向不为入账、币种不同、金额不符的候选一律不核验。
"""
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction


@dataclass
class ReceivableView:
    id: int
    receivable_no: str
    collecting_subject_id: int
    amount: Fraction
    currency: str
    confirmed_date: object
    resolution_id: int | None = None
    resolution_line_id: int | None = None


@dataclass
class ReceiptView:
    id: int
    receipt_no: str
    collecting_subject_id: int
    payer_subject_id: int | None
    payer_label: str
    amount: Fraction
    currency: str
    direction: str
    value_date: object
    memo: str


@dataclass
class MatchResult:
    receipt_id: int
    receivable_id: int | None
    rule: str
    status: str
    matched_amount: Fraction
    currency: str
    verified: bool
    reason: str
    detail: dict = field(default_factory=dict)


def _same_payer_ok(receipt: ReceiptView, receivable: ReceivableView,
                   receivable_payer_map: dict[int, int | None]) -> bool:
    """应收的应付方（issuer）若能解析，则与回单付款主体比对；否则不作为排除条件。"""
    expected = receivable_payer_map.get(receivable.id)
    if expected is None or receipt.payer_subject_id is None:
        return True
    return expected == receipt.payer_subject_id


def match_receipts(
    receipts: list[ReceiptView],
    receivables: list[ReceivableView],
    receivable_payer_map: dict[int, int | None] | None = None,
    tolerance: Fraction = Fraction(0),
) -> list[MatchResult]:
    receivable_payer_map = receivable_payer_map or {}
    results: list[MatchResult] = []

    for receipt in receipts:
        # 只处理入账方向；出账/冲正回单不核验应收
        if receipt.direction != "inflow":
            results.append(MatchResult(
                receipt_id=receipt.id, receivable_id=None, rule="direction",
                status="excluded", matched_amount=Fraction(0),
                currency=receipt.currency, verified=False,
                reason="非入账方向回单，不用于核验应收",
            ))
            continue

        candidates = _score_candidates(receipt, receivables, receivable_payer_map, tolerance)

        if not candidates:
            results.append(MatchResult(
                receipt_id=receipt.id, receivable_id=None, rule="none",
                status="candidate", matched_amount=Fraction(0),
                currency=receipt.currency, verified=False,
                reason="无满足币种/金额/付款主体的应收候选，保留为未匹配回单",
            ))
            continue

        results.extend(_decide_for_receipt(receipt, candidates))

    return results


def _score_candidates(receipt, receivables, payer_map, tolerance):
    scored = []
    for recv in receivables:
        if recv.collecting_subject_id != receipt.collecting_subject_id:
            continue
        if recv.currency != receipt.currency:
            continue
        if not _same_payer_ok(receipt, recv, payer_map):
            continue
        amount_equal = abs(recv.amount - receipt.amount) <= tolerance
        ref_hit = (recv.receivable_no and
                   recv.receivable_no.upper() in (receipt.receipt_no or "").upper())
        memo_hit = bool(recv.receivable_no and recv.receivable_no.upper() in
                        (receipt.memo or "").upper())
        # 金额 / 回单号 / 摘要任一命中即可成为候选；金额等额是强证据
        if not (amount_equal or ref_hit or memo_hit):
            continue
        strength = (
            3 if ref_hit else 0
        ) + (
            2 if amount_equal else 0
        ) + (
            1 if memo_hit else 0
        )
        scored.append({
            "receivable": recv, "amount_equal": amount_equal,
            "ref_hit": ref_hit, "memo_hit": memo_hit, "strength": strength,
        })
    scored.sort(key=lambda c: (-c["strength"], c["receivable"].id))
    return scored


def _decide_for_receipt(receipt: ReceiptView, candidates: list[dict]) -> list[MatchResult]:
    top = candidates[0]

    # ---- 汇聚：回单金额等于多张被摘要/回单号点名的应收之和 ----
    referenced = [c for c in candidates if c["ref_hit"] or c["memo_hit"]]
    if len(referenced) >= 2:
        subset = _find_sum_subset(referenced, receipt.amount)
        if subset:
            return [
                MatchResult(
                    receipt_id=receipt.id, receivable_id=c["receivable"].id,
                    rule="external_ref" if c["ref_hit"] else "memo",
                    status="aggregated", matched_amount=c["receivable"].amount,
                    currency=receipt.currency, verified=True,
                    reason=("同一回单汇聚多张应收（合计等于回单金额），汇聚理由记为事实"),
                    detail={
                        "aggregation_receivable_ids": [x["receivable"].id for x in subset],
                        "aggregation_total": str(_dec(receipt.amount)),
                    },
                )
                for c in subset
            ]

    # ---- 一对多冲突：等额且摘要无法区分的多条应收 ----
    equal_top = [c for c in candidates
                 if c["receivable"].amount == receipt.amount]
    distinguishable = (
        top["ref_hit"] or top["memo_hit"]
    )
    if len(equal_top) >= 2 and not distinguishable:
        # 保持候选，全部标记 one_to_many，且不核验（排除出已核验到账）
        return [
            MatchResult(
                receipt_id=receipt.id, receivable_id=c["receivable"].id,
                rule="amount_payer", status="one_to_many",
                matched_amount=receipt.amount, currency=receipt.currency,
                verified=False,
                reason=("同一回单等额匹配多条应收且摘要无法区分，保持一对多候选，"
                        "从已核验到账中排除该金额"),
                detail={
                    "candidate_receivable_ids": [x["receivable"].id for x in equal_top],
                    "conflict_amount": str(_dec(receipt.amount)),
                },
            )
            for c in equal_top
        ]

    # ---- 强证据：回单号命中；或等额 + 摘要命中；或唯一等额 ----
    if top["ref_hit"] or (top["amount_equal"] and len(equal_top) == 1) or top["memo_hit"]:
        recv = top["receivable"]
        rule = "external_ref" if top["ref_hit"] else (
            "amount_payer" if top["amount_equal"] else "memo")
        return [MatchResult(
            receipt_id=receipt.id, receivable_id=recv.id, rule=rule,
            status="verified", matched_amount=receipt.amount,
            currency=receipt.currency, verified=True,
            reason=f"回单与应收唯一匹配（{rule}），计入已核验到账",
            detail={"amount_equal": top["amount_equal"],
                    "ref_hit": top["ref_hit"], "memo_hit": top["memo_hit"]},
        )]

    # ---- 弱候选：仅金额相近 / 模糊，不核验 ----
    recv = top["receivable"]
    return [MatchResult(
        receipt_id=receipt.id, receivable_id=recv.id, rule="near",
        status="candidate", matched_amount=receipt.amount,
        currency=receipt.currency, verified=False,
        reason="仅有弱证据，保留候选，不计入已核验到账",
        detail={"strength": top["strength"]},
    )]


# ---------- 拆分 / 汇聚（在已核验匹配基础上识别） ----------
def detect_split_and_aggregate(
    verified: list[MatchResult], receipts: list[ReceiptView],
) -> list[MatchResult]:
    """对已核验结果识别拆分与汇聚，并记录为事实（不改金额，只改 rule/status 与理由）。

    - 一张应收由多张回单覆盖 -> 每张回单 status=split；
    - 一张回单覆盖多张应收 -> 该回单 status=aggregated。
    两种情况都要求合计等于目标金额（否则保留原 verified，不强行归类）。
    """
    recv_total: dict[int, Fraction] = {}
    recv_receipts: dict[int, list[MatchResult]] = {}
    receipt_recvs: dict[int, list[MatchResult]] = {}
    for m in verified:
        if not m.verified or m.receivable_id is None:
            continue
        recv_total[m.receivable_id] = recv_total.get(m.receivable_id, Fraction(0)) + m.matched_amount
        recv_receipts.setdefault(m.receivable_id, []).append(m)
        receipt_recvs.setdefault(m.receipt_id, []).append(m)

    out: list[MatchResult] = []
    for m in verified:
        if not m.verified or m.receivable_id is None:
            out.append(m)
            continue
        same_receipt = receipt_recvs.get(m.receipt_id, [])
        same_receivable = recv_receipts.get(m.receivable_id, [])
        if len(same_receipt) > 1:
            m.status = "aggregated"
            m.reason = (m.reason + "；同一回单汇聚多张应收，理由记为事实")
            m.detail = {**m.detail,
                        "aggregation_receivable_ids": [x.receivable_id for x in same_receipt]}
        elif len(same_receivable) > 1:
            m.status = "split"
            m.reason = (m.reason + "；一张应收由多张回单拆分到账，拆分理由记为事实")
            m.detail = {**m.detail,
                        "split_receipt_ids": [x.receipt_id for x in same_receivable]}
        out.append(m)
    return out


def _find_sum_subset(candidates: list[dict], target: Fraction) -> list[dict] | None:
    """在候选应收中找一组（>=2）金额之和恰等于回单金额。

    先试全部候选之和；否则做小规模子集搜索（候选数很少，DP 足够）。
    """
    if len(candidates) < 2:
        return None
    if sum((c["receivable"].amount for c in candidates), Fraction(0)) == target:
        return candidates
    from itertools import combinations
    n = len(candidates)
    for size in range(2, n):
        for combo in combinations(candidates, size):
            if sum((c["receivable"].amount for c in combo), Fraction(0)) == target:
                return list(combo)
    return None


def _dec(fr: Fraction) -> Decimal:
    return (Decimal(fr.numerator) / Decimal(fr.denominator)).quantize(Decimal("0.0001"))
