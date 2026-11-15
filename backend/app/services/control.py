"""表决控制分析（与经济权益完全分开计算）。

算法纪律（对应需求）：
1. 以控制人"直接可行使票数"为起点；未知持有人仍计入总票数，稀释比例，但不能成为控制人。
2. 逐轮扩展：只有已确认受控公司的可支配票数才能累计；达到严格超过半数（>50%）才纳入控制范围，
   迭代至不动点。单调增长 + 仅承认上一轮已确认集合，使互相持股企业无法靠"假定彼此受控"自举。
3. 人工章程结论（带出处）作为已确认种子单独附加，标记 basis=manual；不改动任何经济份额。
"""
from dataclasses import dataclass, field

from .economic import CompanyInputs


@dataclass
class ManualSeed:
    company_id: int
    controller_id: int
    controller_label: str
    reason: str
    provenance: str


@dataclass
class ControlResult:
    findings: list[dict] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)


def analyze_control(
    companies: list[CompanyInputs],
    manual_seeds: list[ManualSeed],
    tol: float = 1e-12,
) -> ControlResult:
    company_ids = [c.company_id for c in companies]
    universe = set(company_ids)
    name_by_id = {c.company_id: c.company_name for c in companies}
    # 外部控制候选人的名称从持有记录收集（自然人/机构不在公司表）
    for c in companies:
        for hld in c.holdings:
            if hld.holder_id is not None:
                name_by_id.setdefault(hld.holder_id, hld.holder_label)
    total_votes = {c.company_id: c.total_votes for c in companies}

    # direct_votes[controller_id][company_id] = 直接票数（仅已知主体可成为控制人）
    direct_votes: dict = {}
    # 公司之间的持票：votes_held[company_j][company_i] = j 持有 i 的票数
    votes_held: dict = {cid: {} for cid in company_ids}
    candidate_controllers: set = set()

    for c in companies:
        for h in c.holdings:
            votes = h.shares * h.votes_per_share
            if h.is_company_in_universe and h.holder_id in universe:
                votes_held[h.holder_id][c.company_id] = (
                    votes_held[h.holder_id].get(c.company_id, 0.0) + votes
                )
                continue
            if h.holder_id is None or h.is_unidentified:
                continue  # 未知持有人：票数留在分母，不具备控制人身份
            direct_votes.setdefault(h.holder_id, {})
            direct_votes[h.holder_id][c.company_id] = (
                direct_votes[h.holder_id].get(c.company_id, 0.0) + votes
            )
            candidate_controllers.add(h.holder_id)

    # 人工种子：按控制人分组，作为第 0 轮已确认控制（带出处，独立留痕）
    manual_by_controller: dict = {}
    findings: list[dict] = []
    for seed in manual_seeds:
        candidate_controllers.add(seed.controller_id)
        manual_by_controller.setdefault(seed.controller_id, {})[seed.company_id] = seed
        findings.append({
            "controlled_company_id": seed.company_id,
            "company_name": name_by_id.get(seed.company_id, str(seed.company_id)),
            "controller_subject_id": seed.controller_id,
            "controller_label": seed.controller_label,
            "basis": "manual",
            "controlled_votes": None,
            "total_votes": total_votes.get(seed.company_id),
            "vote_ratio": None,
            "provenance": seed.provenance,
            "expansion": [{
                "round": 0,
                "reason": seed.reason,
                "provenance": seed.provenance,
            }],
        })

    rounds_log: list = []
    for controller in candidate_controllers:
        # 已确认受控公司集合：人工种子先行
        controlled: set = set(manual_by_controller.get(controller, {}).keys())
        direct = direct_votes.get(controller, {})
        round_no = 0
        while True:
            round_no += 1
            newly_added = []
            for cid in company_ids:
                if cid in controlled:
                    continue
                tv = total_votes[cid]
                if tv <= 0:
                    continue
                direct_part = direct.get(cid, 0.0)
                # 仅累计"已确认受控公司"持有的票数
                controlled_part = 0.0
                contributors = []
                for cj in controlled:
                    v = votes_held.get(cj, {}).get(cid, 0.0)
                    if v > 0:
                        controlled_part += v
                        contributors.append({
                            "controlled_company_id": cj,
                            "controlled_company_name": name_by_id.get(cj, str(cj)),
                            "votes": v,
                        })
                controlled_votes = direct_part + controlled_part
                # 严格超过半数
                if controlled_votes > tv / 2.0 + tol:
                    newly_added.append({
                        "company_id": cid,
                        "direct_votes": direct_part,
                        "controlled_part": controlled_part,
                        "contributors": contributors,
                        "controlled_votes": controlled_votes,
                        "total_votes": tv,
                        "ratio": controlled_votes / tv,
                    })
            if not newly_added:
                break
            for item in newly_added:
                cid = item["company_id"]
                controlled.add(cid)
                # 人工种子已生成 manual 结论的，不覆盖
                if cid in manual_by_controller.get(controller, {}):
                    continue
                basis = "direct" if item["direct_votes"] > tv_half(total_votes, cid) \
                    and item["controlled_part"] <= tol else "expanded"
                findings.append({
                    "controlled_company_id": cid,
                    "company_name": name_by_id.get(cid, str(cid)),
                    "controller_subject_id": controller,
                    "controller_label": name_by_id.get(controller, f"主体#{controller}"),
                    "basis": basis,
                    "controlled_votes": item["controlled_votes"],
                    "total_votes": item["total_votes"],
                    "vote_ratio": item["ratio"],
                    "provenance": None,
                    "expansion": [{
                        "round": round_no,
                        "direct_votes": item["direct_votes"],
                        "controlled_company_votes": item["contributors"],
                        "controlled_part_sum": item["controlled_part"],
                        "rule": "直接票数 + 已确认受控公司可支配票数，严格超过 50%",
                    }],
                })
            rounds_log.append({"controller": controller, "round": round_no,
                               "added": [x["company_id"] for x in newly_added]})
            if round_no > len(company_ids) + 2:  # 单调集合最多 n 轮，防御性上限
                break

    findings.sort(key=lambda f: (f["controlled_company_id"], f["basis"]))
    return ControlResult(
        findings=findings,
        diagnostics={"expansion_rounds": rounds_log, "company_count": len(company_ids)},
    )


def tv_half(total_votes: dict, cid: int) -> float:
    return total_votes.get(cid, 0.0) / 2.0
