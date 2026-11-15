"""求解器单元测试——期望值全部由小规模手算独立推导，不来自程序输出。

手算依据见各测试 docstring；容差 1e-8。
"""
import math

import pytest

from app.services.economic import CompanyInputs, Holding, solve_economic
from app.services.control import ManualSeed, analyze_control
from app.services.graph import tarjan_scc

TOL = 1e-8
SING = 1e-10
COND = 1e10


def h(holder_id, label, shares, inside=False, unknown=False, vps=1.0):
    return Holding(
        issuer_id=0, holder_id=holder_id, holder_label=label,
        is_company_in_universe=inside, is_unidentified=unknown,
        shares=shares, votes_per_share=vps,
    )


def find_interest(result, cid, label_substr):
    for item in result.company_results[cid]["interests"]:
        if label_substr in item["holder_label"]:
            return item
    return None


# ---------- S1 交叉持股联立方程手算 ----------
def test_cross_holding_solvable_by_hand():
    """手算：
    A: 张东明 600/1000=0.6 直接，B 持 A 0.4；B: A 持 B 0.2，晨星 0.8。
    方程 (I-A)x=b，I-A=[[1,-0.4],[-0.2,1]]，行列式 0.92。
      张东明：xA=0.6/0.92=0.65217391，xB=0.2*xA=0.13043478
      晨星  ：xB=0.8/0.92=0.86956522，xA=0.4*xB=0.34782609
    两家公司权益合计均恰为 1。
    """
    A, B, P1, I1 = 1, 2, 100, 101
    companies = [
        CompanyInputs(A, "A", 1000, 1000, [
            h(P1, "张东明", 600), h(B, "B", 400, inside=True)]),
        CompanyInputs(B, "B", 1000, 1000, [
            h(A, "A", 200, inside=True), h(I1, "晨星", 800)]),
    ]
    res = solve_economic(companies, TOL, SING, COND)
    assert res.ok
    assert [sorted(c) for c in res.sccs] == [[A, B]]
    assert res.closed_components == []

    p1_a = find_interest(res, A, "张东明")
    i1_a = find_interest(res, A, "晨星")
    assert p1_a["solve_status"] == "unique"
    assert math.isclose(p1_a["final_share"], 0.6 / 0.92, abs_tol=1e-9)
    assert math.isclose(find_interest(res, B, "张东明")["final_share"],
                        0.12 / 0.92, abs_tol=1e-9)
    assert math.isclose(find_interest(res, B, "晨星")["final_share"],
                        0.8 / 0.92, abs_tol=1e-9)
    assert math.isclose(i1_a["final_share"], 0.32 / 0.92, abs_tol=1e-9)
    # 合计严格为 1
    assert math.isclose(
        sum(x["final_share"] for x in res.company_results[A]["interests"]),
        1.0, abs_tol=1e-9)
    assert math.isclose(
        sum(x["final_share"] for x in res.company_results[B]["interests"]),
        1.0, abs_tol=1e-9)
    # 方程贡献留痕存在且包含经由公司项
    via = [c for c in p1_a["contributions"] if c["kind"] == "via_company"]
    assert via and math.isclose(via[0]["a_ij"], 0.4, abs_tol=1e-12)


# ---------- S2 完全封闭：不可唯一求解，禁止给近似百分比 ----------
def test_closed_loop_non_unique():
    """C: D 持 60%、C 自持 40%；D: C 持 100%。权益无法流到任何外部主体。"""
    C, D = 3, 4
    companies = [
        CompanyInputs(C, "C", 1000, 1000, [
            h(D, "D", 600, inside=True), h(C, "C", 400, inside=True)]),
        CompanyInputs(D, "D", 1000, 1000, [
            h(C, "C", 1000, inside=True)]),
    ]
    res = solve_economic(companies, TOL, SING, COND)
    assert res.ok  # 封闭是确定性结论，不是数值失败
    assert res.closed_components == [[C, D]]
    for cid in (C, D):
        cr = res.company_results[cid]
        assert cr["solve_status"] == "non_unique"
        assert cr["interests"] == []          # 不编造外部持有人百分比
        assert math.isclose(cr["unallocatable"], 1.0, abs_tol=1e-9)


# ---------- 未知持有人保留独立份额，不归一 ----------
def test_unknown_holder_kept_independent():
    M, P1 = 13, 100
    companies = [CompanyInputs(M, "M", 1000, 1000, [
        h(P1, "张东明", 600), h(None, "未知持有人", 400, unknown=True)])]
    res = solve_economic(companies, TOL, SING, COND)
    assert res.ok
    known = find_interest(res, M, "张东明")
    unknown = find_interest(res, M, "未知持有人")
    assert math.isclose(known["final_share"], 0.6, abs_tol=1e-12)  # 未被归一成 1.0
    assert math.isclose(unknown["final_share"], 0.4, abs_tol=1e-12)
    assert unknown["is_unidentified"] is True


# ---------- S3 股权票权分离 + 控制扩展不动点 ----------
def test_control_vote_equity_separation_and_expansion():
    """F 总票数 6000*1 + 4000*10 = 46000。王辅庭 40000 票=86.96% 直接控制 F，
    经济权益只有 40%；F 控制 G(51%)，G 控制 H(60%)，控制经两轮扩展到 H。"""
    F, G, H, P2, P3, I2 = 6, 7, 8, 200, 201, 300
    companies = [
        CompanyInputs(F, "F", 10000, 46000, [
            h(P2, "李若兰", 4500, vps=1),
            h(P3, "王辅庭", 4000, vps=10),
            h(I2, "远海", 1500, vps=1)]),
        CompanyInputs(G, "G", 1000, 1000, [
            h(F, "F", 510, inside=True), h(P2, "李若兰", 290),
            h(999, "昌隆", 200)]),
        CompanyInputs(H, "H", 1000, 1000, [
            h(G, "G", 600, inside=True), h(100, "张东明", 400)]),
    ]
    res = analyze_control(companies, [])
    by = {(f["controller_subject_id"], f["controlled_company_id"]): f for f in res.findings}
    assert by[(P3, F)]["basis"] == "direct"
    assert math.isclose(by[(P3, F)]["vote_ratio"], 40000 / 46000, abs_tol=1e-9)
    assert by[(P3, G)]["basis"] == "expanded"
    assert by[(P3, H)]["basis"] == "expanded"
    # 李若兰经济/票数均不过半，任何公司都不归她控制
    assert not any(f["controller_subject_id"] == P2 for f in res.findings)
    # 张东明直接 40% 且不控制任何公司，H 不归他
    assert not any(f["controller_subject_id"] == 100 for f in res.findings)
    # 扩展依据链留痕
    assert by[(P3, H)]["expansion"][0]["round"] >= 2


# ---------- 环形持股不得自举控制 ----------
def test_cross_holding_cannot_bootstrap_control():
    """X、Y 互持 60%，Z 各持 40%。任何一方都无法启动控制，结论为空。"""
    X, Y, Z = 21, 22, 23
    companies = [
        CompanyInputs(X, "X", 1000, 1000, [
            h(Y, "Y", 600, inside=True), h(Z, "Z", 400)]),
        CompanyInputs(Y, "Y", 1000, 1000, [
            h(X, "X", 600, inside=True), h(Z, "Z", 400)]),
    ]
    res = analyze_control(companies, [])
    assert res.findings == []
    # 经济上仍可唯一求解（非封闭），证明"控制不可自举"与经济求解互不干扰
    econ = solve_economic(companies, TOL, SING, COND)
    assert econ.ok and econ.closed_components == []
    zx = find_interest(econ, X, "Z")
    # x_X = 0.4 + 0.6·x_Y, x_Y = 0.4 + 0.6·x_X；行列式 1-0.36=0.64，
    # x_X = (0.4 + 0.6·0.4)/0.64 = 0.64/0.64 = 1.0，Z 穿透环形持股最终享有全部权益
    assert math.isclose(zx["final_share"], 1.0, abs_tol=1e-9)


# ---------- 章程人工控制：独立附加，不改经济份额 ----------
def test_manual_control_does_not_change_economics():
    N, P5, P1 = 14, 400, 100
    companies = [CompanyInputs(N, "N", 1000, 1000, [
        h(P5, "周衡之", 200), h(P1, "张东明", 800)])]
    seeds = [ManualSeed(N, P5, "周衡之", "章程第18条", "丑公司章程第18条")]
    ctrl = analyze_control(companies, seeds)
    bases = {(f["controller_subject_id"], f["basis"]) for f in ctrl.findings}
    assert (P5, "manual") in bases
    assert (P1, "direct") in bases  # 算法结论与人工结论并列，不互相覆盖
    econ = solve_economic(companies, TOL, SING, COND)
    assert math.isclose(find_interest(econ, N, "周衡之")["final_share"], 0.2)
    assert math.isclose(find_interest(econ, N, "张东明")["final_share"], 0.8)


# ---------- Tarjan SCC 基本性质 ----------
def test_tarjan_detects_cycle_and_self_loop():
    nodes = [1, 2, 3, 4]
    edges = [(1, 2), (2, 1), (3, 3), (1, 4)]
    sccs = tarjan_scc(nodes, edges)
    assert [1, 2] in sccs
    assert [3] in sccs
    assert all(4 not in c for c in sccs)


# ---------- 严格过半：恰好 50% 不构成控制 ----------
def test_exact_half_is_not_control():
    X, Z = 31, 32
    companies = [CompanyInputs(X, "X", 1000, 1000, [
        h(Z, "Z", 500), h(None, "未知持有人", 500, unknown=True)])]
    res = analyze_control(companies, [])
    assert res.findings == []
