"""经济权益求解器。

模型（需求原文的联立方程）：
    对每家公司 i 与每个外部持有人 h：
        x[i,h] = b[i,h] + Σ_j A[i,j] · x[j,h]
    其中 A[i,j] 是公司 j 直接持有公司 i 的股份比例，b[i,h] 是外部持有人 h 的直接比例，
    x[i,h] 是 h 在 i 中享有的最终经济份额。矩阵形式 (I - A)X = B。

关键纪律：
- 先做 SCC 分解找出互相持股部分；完全封闭（无任何外部持有人渗入）的 SCC 不可唯一求解，
  报告 non_unique 与不可分配份额，绝不靠截断遍历深度给近似百分比。
- 未知持有人是独立的 B 列，保留自身份额，从不把已知部分重新归一化为 100%。
- SVD 检测秩亏损与条件数；残差或公司权益合计超容差、条件数超限时停止正式出数并列出公司。
"""
from dataclasses import dataclass, field

import numpy as np

from .graph import all_sccs


@dataclass
class Holding:
    issuer_id: int           # 发行公司
    holder_id: int | None    # 已知主体 id；未知持有人为 None
    holder_label: str
    is_company_in_universe: bool  # 持有人是否为分析范围内的公司（进入 A，否则进入 B）
    is_unidentified: bool
    shares: float
    votes_per_share: float


@dataclass
class CompanyInputs:
    company_id: int
    company_name: str
    total_shares: float                 # 各类别登记总量之和
    total_votes: float                  # Σ 股份数 × 每股表决权
    holdings: list[Holding] = field(default_factory=list)


@dataclass
class SolveResult:
    ok: bool
    failure_reason: str | None = None
    unstable_companies: list = field(default_factory=list)
    # 每家公司的方程描述与每个外部持有人的最终份额
    company_results: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)
    sccs: list = field(default_factory=list)
    closed_components: list = field(default_factory=list)


def _direct_fractions(companies: list[CompanyInputs]) -> tuple[dict, dict]:
    """返回 (direct[company_id][holder_key] = 比例, holder_meta)。

    holder_key: ("s", subject_id) 已知主体；("u", label) 未知持有人独立份额。
    经济比例按股份数 / 登记总量计算，与表决权无关。
    """
    direct: dict = {}
    for c in companies:
        agg: dict = {}
        for h in c.holdings:
            key = ("u", h.holder_label) if h.is_unidentified or h.holder_id is None \
                else ("s", h.holder_id)
            agg[key] = agg.get(key, 0.0) + h.shares / c.total_shares
        direct[c.company_id] = agg
    return direct, None


def solve_economic(companies: list[CompanyInputs], tol: float,
                   singular_threshold: float, condition_limit: float) -> SolveResult:
    company_ids = [c.company_id for c in companies]
    idx = {cid: k for k, cid in enumerate(company_ids)}
    n = len(companies)
    if n == 0:
        return SolveResult(ok=True, diagnostics={"companies": 0})

    direct, _ = _direct_fractions(companies)

    # 范围外公司持有人也作为外部主体（无法穿透），key 用 ("s", id)
    universe = set(company_ids)
    A = np.zeros((n, n))
    external_keys: set = set()
    for c in companies:
        for h in c.holdings:
            frac = h.shares / c.total_shares
            i = idx[c.company_id]
            if h.is_company_in_universe and h.holder_id in universe:
                j = idx[h.holder_id]
                A[i, j] += frac
            else:
                key = ("u", h.holder_label) if h.is_unidentified or h.holder_id is None \
                    else ("s", h.holder_id)
                external_keys.add(key)

    # ---------- 1. SCC 与封闭性判定 ----------
    edges = [(company_ids[i], company_ids[j])
             for i in range(n) for j in range(n) if A[i, j] > 0]
    sccs = all_sccs(company_ids, edges)
    cyclic_sccs = [s for s in sccs if len(s) > 1 or
                   (len(s) == 1 and A[idx[s[0]], idx[s[0]]] > 0)]

    closed_set: set = set()
    closed_components: list = []
    for comp in sccs:
        comp_idx = [idx[x] for x in comp]
        is_closed = True
        for i in comp_idx:
            inside = sum(A[i, j] for j in comp_idx)
            # 该公司份额只要有任何流向组件外（含外部持有人），就不是封闭的
            if abs(inside - 1.0) > tol:
                is_closed = False
                break
        if is_closed:
            closed_set.update(comp)
            closed_components.append(sorted(comp))

    # ---------- 2. 封闭组件作为不可穿透终端，构造可解子系统 ----------
    open_ids = [cid for cid in company_ids if cid not in closed_set]
    open_idx = {cid: k for k, cid in enumerate(open_ids)}
    m = len(open_ids)

    # B：m 行 × p 个外部持有人列（含封闭公司列，其份额无法再向下穿透）
    holder_columns: list = sorted(external_keys, key=lambda k: (k[0], str(k[1])))
    for cid in sorted(closed_set):
        holder_columns.append(("closed", cid))
    col_index = {k: p for p, k in enumerate(holder_columns)}

    B = np.zeros((m, len(holder_columns)))
    A_open = np.zeros((m, m))
    # 方程贡献留痕：contrib[company_id][holder_key] = [(来源公司j, A_ij, x_jh 占位)...]
    equation_parts: dict = {cid: {"direct": {}, "via": []} for cid in open_ids}

    for cid in open_ids:
        i = open_idx[cid]
        for key, frac in direct[cid].items():
            if key in col_index:
                B[i, col_index[key]] += frac
                equation_parts[cid]["direct"][_label_key(key)] = frac
        # 范围内公司持有
        for j_global, cid_j in enumerate(company_ids):
            frac = A[idx[cid], j_global]
            if frac <= 0:
                continue
            if cid_j in closed_set:
                B[i, col_index[("closed", cid_j)]] += frac
            else:
                A_open[i, open_idx[cid_j]] += frac
                equation_parts[cid]["via"].append(
                    {"holder_company_id": cid_j, "a_ij": frac}
                )

    # ---------- 3. SVD 求解与数值稳定性判定 ----------
    M = np.eye(m) - A_open
    unstable: list = []
    if m > 0:
        u, s, vt = np.linalg.svd(M)
        s_max = s[0] if len(s) else 0.0
        s_min = s[-1] if len(s) else 0.0
        cond = float(s_max / s_min) if s_min > 0 else float("inf")
        rank = int(np.sum(s > singular_threshold * (s_max or 1.0)))

        if rank < m or not np.isfinite(cond) or cond > condition_limit:
            # 理论上封闭组件剔除后应满秩；仍亏秩/病态说明数据异常，停止正式出数
            involved = open_ids if rank < m else _sensitive_companies(M, open_ids, tol)
            return SolveResult(
                ok=False,
                failure_reason=(
                    f"系数矩阵秩亏损（rank={rank}/{m}），存在未识别的封闭持股结构"
                    if rank < m else f"条件数 {cond:.3e} 超阈值 {condition_limit:.0e}，数值不稳定"
                ),
                unstable_companies=sorted(set(involved)),
                diagnostics={"condition_number": cond, "rank": rank, "size": m},
                sccs=cyclic_sccs,
                closed_components=closed_components,
            )

        sinv = np.diag(1.0 / s)
        M_inv = vt.T @ sinv @ u.T
        X = M_inv @ B
        residuals = np.abs(M @ X - B)
        max_res = float(residuals.max()) if residuals.size else 0.0
        if max_res > tol * max(1.0, float(np.abs(B).max())):
            bad = sorted({open_ids[i] for i in np.where(
                (residuals > tol).any(axis=1))[0]})
            return SolveResult(
                ok=False,
                failure_reason=f"方程残差 {max_res:.3e} 超过容差 {tol:.0e}",
                unstable_companies=bad,
                diagnostics={"max_residual": max_res},
                sccs=cyclic_sccs, closed_components=closed_components,
            )
    else:
        X = np.zeros((0, len(holder_columns)))
        cond = None

    # ---------- 4. 组装结果，并校验每家公司权益合计 ----------
    name_by_id = {c.company_id: c.company_name for c in companies}
    # 外部持有人（非分析范围内公司）的名称从持有记录收集
    holder_names: dict = {}
    for c in companies:
        for hld in c.holdings:
            if hld.holder_id is not None:
                holder_names.setdefault(hld.holder_id, hld.holder_label)
    company_results: dict = {}
    sum_violations: list = []

    for cid in open_ids:
        i = open_idx[cid]
        row_sum = float(X[i, :].sum())
        if abs(row_sum - 1.0) > tol * max(1.0, abs(row_sum), 1.0):
            sum_violations.append(cid)
        interests = []
        for p, key in enumerate(holder_columns):
            val = float(X[i, p])
            if abs(val) <= tol:
                continue
            # 方程贡献：直接项 + 各路径公司项 A_ij * x_jh
            contributions = []
            d = equation_parts[cid]["direct"].get(_label_key(key))
            if d:
                contributions.append({"kind": "direct", "fraction": d})
            for part in equation_parts[cid]["via"]:
                cj = part["holder_company_id"]
                if cj in open_idx:
                    xj = float(X[open_idx[cj], p])
                    if abs(part["a_ij"] * xj) > tol:
                        contributions.append({
                            "kind": "via_company",
                            "holder_company_id": cj,
                            "holder_company_name": name_by_id.get(cj, str(cj)),
                            "a_ij": part["a_ij"],
                            "x_jh": xj,
                            "product": part["a_ij"] * xj,
                        })
            interests.append({
                "holder_key": _label_key(key),
                "holder_subject_id": key[1] if key[0] == "s" else None,
                "holder_label": _holder_label(key, name_by_id, holder_names),
                "is_unidentified": key[0] == "u",
                "is_closed_terminal": key[0] == "closed",
                "final_share": val,
                "solve_status": "unique",
                "contributions": contributions,
            })
        interests.sort(key=lambda z: -z["final_share"])
        company_results[cid] = {
            "company_name": name_by_id[cid],
            "solve_status": "unique",
            "interests": interests,
            "sum_of_shares": row_sum,
            "unallocatable": 0.0,
            "equation": _equation_text(cid, equation_parts[cid], name_by_id),
        }

    # 封闭组件：不唯一、不可分配；仍列出直接可见的外部渗入（按定义为 0，保留结构以便解释）
    for cid in sorted(closed_set):
        leak = sum(v for k, v in direct[cid].items()
                   if k[0] in ("u", "s") and not (k[0] == "s" and k[1] in closed_set))
        company_results[cid] = {
            "company_name": name_by_id[cid],
            "solve_status": "non_unique",
            "interests": [],
            "sum_of_shares": leak,
            "unallocatable": 1.0 - leak,
            "equation": _equation_text(cid, {"direct": direct[cid], "via": []}, name_by_id),
            "closed_component": sorted(c for c in closed_components if cid in c),
        }
        if leak > tol:  # 封闭判定保证不会发生，防御性分支
            sum_violations.append(cid)

    if sum_violations:
        return SolveResult(
            ok=False,
            failure_reason=f"公司权益合计不等于 1（容差 {tol:.0e}），停止正式出数",
            unstable_companies=sorted(sum_violations),
            company_results=company_results,
            diagnostics={"condition_number": cond},
            sccs=cyclic_sccs, closed_components=closed_components,
        )

    return SolveResult(
        ok=True,
        company_results=company_results,
        diagnostics={
            "condition_number": cond,
            "max_residual": float(residuals.max()) if m and residuals.size else 0.0,
            "companies": n,
            "external_holders": len(holder_columns),
            "tolerance": tol,
        },
        sccs=cyclic_sccs,
        closed_components=closed_components,
    )


def _sensitive_companies(M: np.ndarray, open_ids: list, tol: float) -> list:
    """条件数超限时，列出对角占优最弱、对误差最敏感的公司。"""
    diag = np.abs(np.diag(M))
    off = np.abs(M).sum(axis=1) - diag
    weakness = off / np.maximum(diag, 1e-300)
    order = np.argsort(-weakness)
    return [open_ids[i] for i in order[: max(1, len(open_ids) // 2)]]


def _label_key(key: tuple) -> str:
    return f"{key[0]}:{key[1]}"


def _holder_label(key: tuple, name_by_id: dict, holder_names: dict | None = None) -> str:
    kind, val = key
    if kind == "u":
        return str(val)
    if kind == "closed":
        return f"封闭持股主体：{name_by_id.get(val, val)}（权益不可穿透）"
    holder_names = holder_names or {}
    return holder_names.get(val) or name_by_id.get(val, f"主体#{val}")


def _equation_text(cid: int, parts: dict, name_by_id: dict) -> str:
    terms = []
    for label, frac in sorted(parts.get("direct", {}).items()):
        terms.append(f"{frac:.6f}·[{label}]")
    for via in parts.get("via", []):
        terms.append(
            f"{via['a_ij']:.6f}·x({name_by_id.get(via['holder_company_id'], via['holder_company_id'])})"
        )
    return f"x({name_by_id.get(cid, cid)}) = " + " + ".join(terms if terms else ["0"])
