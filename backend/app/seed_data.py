"""虚构演示样本（企业 / 自然人 / 机构股东均为虚构，不连接工商网站）。

四个必备场景：
  S1 交叉持股可求解：甲集团(A) 与 乙产业(B) 环形持股，外部人权益经联立方程唯一确定。
  S2 完全封闭不可定：丙闭环(C) 与 丁闭环(D) 权益只在彼此间流动，无外部渗入，报告 non_unique。
  S3 股权与票权分离：戊科技(F) 优先股一股十票，少数经济权益持有人掌握过半票数；
     并演示 F -> 己(G) -> 庚(H) 的控制扩展不动点。
  S4 增资更正：辛股份(K) 增资前 1000 股 / 增资后 2000 股两份名册，有效区间衔接、更正留痕，
     任何一次分析只取当时一份，绝不拼接。

另含：未知持有人独立份额(壬实业 M)、同名候选与标识冲突(癸商贸 O / 己 G)、
章程人工控制(丑公司 N)、部分失败名册(寅制造 R)。
所有数字均可由 tests/ 下的手算样本独立核对。
"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models
from .services.import_service import import_roster, resume_import
from .schemas import RosterImportIn, RosterRowIn
from .services.revisions import create_revision

D = date


def _row(holder, code, htype, cls, shares, vps=1, is_total=False, total=None,
         start=D(2026, 1, 1), end=None, prov="虚构名册"):
    return RosterRowIn(
        holder_name=None if is_total else holder,
        holder_unified_code=code,
        holder_type=htype,
        share_class=cls, shares=shares, votes_per_share=vps,
        is_total=is_total, registered_total=total,
        effective_from=start, effective_to=end, provenance=prov,
    )


def total_row(cls, total, vps=1, start=D(2026, 1, 1), end=None):
    return _row(None, None, "company", cls, total, vps, is_total=True, total=total,
                start=start, end=end, prov="登记总量/虚构工商档案摘要")


# 主体统一标识（虚构信用代码）
CODE_A = "91310000MA1FL0001A"
CODE_B = "91310000MA1FL0002B"
CODE_C = "91310000MA1FL0003C"
CODE_D = "91310000MA1FL0004D"
CODE_F = "91310000MA1FL0006F"
CODE_G = "91310000MA1FL0007G"
CODE_H = "91310000MA1FL0008H"
CODE_K = "91310000MA1FL0011K"
CODE_M = "91310000MA1FL0013M"
CODE_N = "91310000MA1FL0014N"
CODE_O = "91310000MA1FL0015O"
CODE_R = "91310000MA1FL0018R"

P1 = ("张东明", "31010119900101001X", "person")
P2 = ("李若兰", "31010119920305002X", "person")
P3 = ("王辅庭", "31010119880715003X", "person")
P4 = ("赵启年", "31010119851120004X", "person")
P5 = ("周衡之", "31010119800202005X", "person")
I1 = ("晨星股权投资基金（虚构）", "91310000MA1FL0101I", "institution")
I2 = ("远海资管（虚构）", "91310000MA1FL0102I", "institution")
I3 = ("同裕产业基金（虚构）", "91310000MA1FL0103I", "institution")


def _doc(source_ref, title, company, code, start, end, rows, corrects=None):
    return RosterImportIn(
        source_ref=source_ref, doc_title=title,
        company_name=company, company_unified_code=code,
        effective_from=start, effective_to=end,
        corrects_source_ref=corrects, rows=rows,
    )


def build_payloads() -> list[RosterImportIn]:
    payloads: list[RosterImportIn] = []

    # ---------- S1 交叉持股可求解 ----------
    # A: 张东明 600 + B 400；B: A 200 + 晨星 800。总量各 1000。
    payloads.append(_doc(
        "SRC-A-2026", "甲集团 2026 年股东名册", "甲集团股份有限公司（虚构）", CODE_A,
        D(2026, 1, 1), None, [
            total_row("common", 1000),
            _row(P1[0], P1[1], P1[2], "common", 600, prov="甲集团名册第3页"),
            _row("乙产业有限公司（虚构）", CODE_B, "company", "common", 400,
                 prov="甲集团名册第4页"),
        ]))
    payloads.append(_doc(
        "SRC-B-2026", "乙产业 2026 年股东名册", "乙产业有限公司（虚构）", CODE_B,
        D(2026, 1, 1), None, [
            total_row("common", 1000),
            _row("甲集团股份有限公司（虚构）", CODE_A, "company", "common", 200,
                 prov="乙产业名册第2页"),
            _row(I1[0], I1[1], I1[2], "common", 800, prov="乙产业名册第5页"),
        ]))

    # ---------- S2 完全封闭 ----------
    # C: D 持 600、C 自持 400；D: C 持 1000。无任何外部持有人。
    payloads.append(_doc(
        "SRC-C-2026", "丙闭环 2026 年股东名册", "丙闭环有限公司（虚构）", CODE_C,
        D(2026, 1, 1), None, [
            total_row("common", 1000),
            _row("丁闭环有限公司（虚构）", CODE_D, "company", "common", 600,
                 prov="丙闭环名册第1页"),
            _row("丙闭环有限公司（虚构）", CODE_C, "company", "common", 400,
                 prov="丙闭环名册第1页（库存股自持）"),
        ]))
    payloads.append(_doc(
        "SRC-D-2026", "丁闭环 2026 年股东名册", "丁闭环有限公司（虚构）", CODE_D,
        D(2026, 1, 1), None, [
            total_row("common", 1000),
            _row("丙闭环有限公司（虚构）", CODE_C, "company", "common", 1000,
                 prov="丁闭环名册第1页"),
        ]))

    # ---------- S3 股权 / 票权分离 + 控制扩展 ----------
    # F: 普通 6000 股一股一票；优先 4000 股一股十票。
    #    李若兰 common 4500（经济45%）；王辅庭 preferred 4000（经济40%、票 40000）；远海 common 1500。
    payloads.append(_doc(
        "SRC-F-2026", "戊科技 2026 年股东名册（含优先股）", "戊科技股份有限公司（虚构）", CODE_F,
        D(2026, 1, 1), None, [
            total_row("common", 6000, vps=1),
            total_row("preferred", 4000, vps=10),
            _row(P2[0], P2[1], P2[2], "common", 4500, vps=1, prov="戊科技名册第2页"),
            _row(P3[0], P3[1], P3[2], "preferred", 4000, vps=10,
                 prov="戊科技名册第3页（章程约定优先股一股十票）"),
            _row(I2[0], I2[1], I2[2], "common", 1500, vps=1, prov="戊科技名册第4页"),
        ]))
    # G: F 直接持 510/1000 -> F 控制 G；昌隆贸易（无标识）200 制造同名候选
    payloads.append(_doc(
        "SRC-G-2026", "己智造 2026 年股东名册", "己智造有限公司（虚构）", CODE_G,
        D(2026, 1, 1), None, [
            total_row("common", 1000),
            _row("戊科技股份有限公司（虚构）", CODE_F, "company", "common", 510,
                 prov="己智造名册第1页"),
            _row("昌隆贸易行（虚构）", None, "company", "common", 200,
                 prov="己智造名册第2页（无统一标识）"),
            _row(P2[0], P2[1], P2[2], "common", 290, prov="己智造名册第2页"),
        ]))
    # H: G 持 600，张东明 400；控制人经 F->G 链条扩展到 H（直接票数为 0）
    payloads.append(_doc(
        "SRC-H-2026", "庚物流 2026 年股东名册", "庚物流有限公司（虚构）", CODE_H,
        D(2026, 1, 1), None, [
            total_row("common", 1000),
            _row("己智造有限公司（虚构）", CODE_G, "company", "common", 600,
                 prov="庚物流名册第1页"),
            _row(P1[0], P1[1], P1[2], "common", 400, prov="庚物流名册第3页"),
        ]))

    # ---------- S4 增资更正：前后两份，总量不同，区间衔接 ----------
    payloads.append(_doc(
        "SRC-K-V1", "辛股份增资前名册（2025 上半年）", "辛股份有限公司（虚构）", CODE_K,
        D(2025, 1, 1), D(2025, 6, 30), [
            total_row("common", 1000, start=D(2025, 1, 1), end=D(2025, 6, 30)),
            _row(P4[0], P4[1], P4[2], "common", 700,
                 start=D(2025, 1, 1), end=D(2025, 6, 30), prov="辛股份旧名册第1页"),
            _row(I3[0], I3[1], I3[2], "common", 300,
                 start=D(2025, 1, 1), end=D(2025, 6, 30), prov="辛股份旧名册第1页"),
        ]))
    payloads.append(_doc(
        "SRC-K-V2", "辛股份增资后更正名册（2025 下半年起）", "辛股份有限公司（虚构）", CODE_K,
        D(2025, 7, 1), None, [
            total_row("common", 2000, start=D(2025, 7, 1)),
            _row(P4[0], P4[1], P4[2], "common", 1200,
                 start=D(2025, 7, 1), prov="增资协议虚构编号 ZZ-2025-07"),
            _row(I3[0], I3[1], I3[2], "common", 800,
                 start=D(2025, 7, 1), prov="增资协议虚构编号 ZZ-2025-07"),
        ], corrects="SRC-K-V1"))

    # ---------- 未知持有人独立份额（不归一） ----------
    payloads.append(_doc(
        "SRC-M-2026", "壬实业 2026 年股东名册（含未知持有人）", "壬实业有限公司（虚构）", CODE_M,
        D(2026, 1, 1), None, [
            total_row("common", 1000),
            _row(P1[0], P1[1], P1[2], "common", 600, prov="壬实业名册第1页"),
            _row("未知持有人", None, "person", "common", 400,
                 prov="壬实业名册第2页（代持尚未识别，保留独立份额）"),
        ]))

    # ---------- 章程人工控制：周衡之仅 20% 经济权益，章程授予控制权 ----------
    payloads.append(_doc(
        "SRC-N-2026", "丑公司 2026 年股东名册", "丑公司有限公司（虚构）", CODE_N,
        D(2026, 1, 1), None, [
            total_row("common", 1000),
            _row(P5[0], P5[1], P5[2], "common", 200, prov="丑公司名册第1页"),
            _row(P1[0], P1[1], P1[2], "common", 800, prov="丑公司名册第1页"),
            _row("昌隆贸易行（虚构）", None, "company", "common", 0,
                 prov="占位：与己智造名册同名主体匹配"),
        ]))

    # ---------- 同名 + 标识冲突候选 ----------
    payloads.append(_doc(
        "SRC-O-2026", "癸商贸 2026 年股东名册", "癸商贸有限公司（虚构）", CODE_O,
        D(2026, 1, 1), None, [
            total_row("common", 1000),
            _row("恒信顾问事务所（虚构）", "91310000MA1FL0201X", "institution",
                 "common", 300, prov="癸商贸名册第1页"),
            _row("恒信顾问事务所（虚构）", "91310000MA1FL0202X", "institution",
                 "common", 300, prov="癸商贸名册第2页（同名不同标识，禁止归并）"),
            _row(P1[0], P1[1], P1[2], "common", 400, prov="癸商贸名册第3页"),
        ]))

    # ---------- 部分失败名册：含一行负股份，导入后 partial_failed，修复后续导 ----------
    payloads.append(_doc(
        "SRC-R-2026", "寅制造 2026 年股东名册（含错误行）", "寅制造有限公司（虚构）", CODE_R,
        D(2026, 1, 1), None, [
            total_row("common", 1000),
            _row(P2[0], P2[1], P2[2], "common", 550, prov="寅制造名册第1页"),
            _row(I2[0], I2[1], I2[2], "common", -50,
                 prov="寅制造名册第2页（录入错误，应为 50）"),
            _row(P1[0], P1[1], P1[2], "common", 400, prov="寅制造名册第3页"),
        ]))
    return payloads


async def seed_all(db: AsyncSession, user: str = "admin") -> dict:
    """幂等播种：已存在的 source_ref 跳过。返回导入摘要。"""
    existing = set((await db.scalars(select(models.RosterDocument.source_ref))).all())
    summary = {"imported": [], "skipped": []}
    for payload in build_payloads():
        if payload.source_ref in existing:
            summary["skipped"].append(payload.source_ref)
            continue
        result = await import_roster(db, payload, user)
        summary["imported"].append({
            "source_ref": payload.source_ref,
            "status": result["process_status"],
            "failed_rows": result["failed_rows"],
            "message": result["message"],
        })

    # 人工章程控制结论（丑公司 N 由周衡之依章程控制），需审核后才生效
    n_company = await db.scalar(
        select(models.Subject).where(models.Subject.unified_code == CODE_N)
    )
    p5 = await db.scalar(
        select(models.Subject).where(models.Subject.unified_code == P5[1])
    )
    if n_company and p5:
        exists = await db.scalar(
            select(models.ManualControlAssertion.id).where(
                models.ManualControlAssertion.company_subject_id == n_company.id
            )
        )
        if not exists:
            assertion = models.ManualControlAssertion(
                company_subject_id=n_company.id,
                controller_subject_id=p5.id,
                reason="章程第18条约定：周衡之委派董事会过半数董事，享有公司控制权",
                provenance="丑公司章程（虚构文本）第18条，2026-01-01 版",
                created_by=user,
            )
            db.add(assertion)
            await db.flush()
            rev = await create_revision(
                db, models.REV_CONTROL, assertion.id,
                "人工附加章程控制结论（不修改经济份额）", user,
            )
            assertion.revision_id = rev.id
            # 样本中直接置为已审核，便于演示；真实流程须 reviewer 审批
            rev.status = models.ST_APPROVED
            rev.reviewed_by = "reviewer"

    # 自动审核所有完整名册（含总量校验）；partial 的 R 保持未审核
    from .services.review import check_class_totals
    docs = list(await db.scalars(select(models.RosterDocument)))
    approved = 0
    for doc in docs:
        rev = await db.get(models.Revision, doc.revision_id)
        if rev.status != models.ST_DRAFT:
            continue
        if doc.process_status == models.DOC_PARTIAL:
            continue
        problems = await check_class_totals(db, doc, 1e-8)
        if not problems:
            rev.status = models.ST_APPROVED
            rev.reviewed_by = "reviewer"
            approved += 1
    summary["auto_approved"] = approved
    await db.commit()

    # 财务事实层验收样本（利润来源穿透 / 跨期对账 / 重述 / 汇率缺失 / 权属待证）
    from .seed_finance import seed_finance
    summary["finance"] = await seed_finance(db, user)
    await db.commit()
    return summary
