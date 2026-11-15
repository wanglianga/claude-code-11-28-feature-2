"""分析编排：时点名册选取、求解器调用、结果快照持久化。"""
import json
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models
from ..config import settings
from .control import ManualSeed, analyze_control
from .economic import CompanyInputs, Holding, solve_economic


async def _effective_approved_docs(db: AsyncSession, as_of: date) -> dict:
    """每个公司选取分析时点有效且已审核的名册；同一公司多于一份则报错，不做拼接。"""
    docs = list(await db.scalars(
        select(models.RosterDocument)
        .where(models.RosterDocument.process_status.in_(
            [models.DOC_IMPORTED, models.DOC_PARTIAL]))
    ))
    by_company: dict = {}
    skipped: list[dict] = []
    for doc in docs:
        rev = await db.get(models.Revision, doc.revision_id)
        # 已审核修订有效；被更正(superseded)的修订对其有效区间内的历史时点仍有效，
        # 是否同一时点出现多份由下面的区间与冲突检查保证
        if not rev or rev.status not in (models.ST_APPROVED, models.ST_SUPERSEDED):
            continue
        if doc.effective_from > as_of:
            continue
        if doc.effective_to is not None and doc.effective_to < as_of:
            continue
        if doc.process_status == models.DOC_PARTIAL:
            skipped.append({"document_id": doc.id, "reason": "存在失败行，未完整导入"})
            continue
        by_company.setdefault(doc.company_subject_id, []).append(doc)

    conflicts = []
    chosen: dict = {}
    for cid, docs_of_company in by_company.items():
        if len(docs_of_company) > 1:
            conflicts.append({
                "company_subject_id": cid,
                "document_ids": [d.id for d in docs_of_company],
                "reason": "同一时点存在多份有效已审核名册，须先撤回/更正，禁止拼成一次分析",
            })
        else:
            chosen[cid] = docs_of_company[0]
    return chosen, conflicts, skipped


async def _build_inputs(
    db: AsyncSession, chosen: dict
) -> tuple[list[CompanyInputs], dict]:
    companies: list[CompanyInputs] = []
    name_by_id: dict = {}
    universe = set(chosen.keys())
    for cid, doc in chosen.items():
        company = await db.get(models.Subject, cid)
        name_by_id[cid] = company.name
        rows = list(await db.scalars(
            select(models.RosterRow).where(
                models.RosterRow.document_id == doc.id,
                models.RosterRow.process_status == models.ROW_OK,
            )
        ))
        total_shares = 0.0
        total_votes = 0.0
        holdings: list[Holding] = []
        for r in rows:
            if r.is_total:
                total_shares += float(r.registered_total)
                total_votes += float(r.registered_total) * float(r.votes_per_share)
                continue
            shares = float(r.shares)
            vps = float(r.votes_per_share)
            holder_id = r.holder_subject_id
            if holder_id is None:
                holdings.append(Holding(
                    issuer_id=cid, holder_id=None,
                    holder_label=r.holder_raw_name or "未知持有人",
                    is_company_in_universe=False, is_unidentified=True,
                    shares=shares, votes_per_share=vps,
                ))
            else:
                holder = await db.get(models.Subject, holder_id)
                holdings.append(Holding(
                    issuer_id=cid, holder_id=holder_id,
                    holder_label=holder.name if holder else f"主体#{holder_id}",
                    is_company_in_universe=holder_id in universe,
                    is_unidentified=False,
                    shares=shares, votes_per_share=vps,
                ))
        companies.append(CompanyInputs(
            company_id=cid, company_name=company.name,
            total_shares=total_shares, total_votes=total_votes, holdings=holdings,
        ))
    return companies, name_by_id


async def run_analysis(db: AsyncSession, as_of: date, user: str) -> dict:
    chosen, conflicts, skipped = await _effective_approved_docs(db, as_of)
    if conflicts:
        return {"ok": False, "reason": "名册冲突", "conflicts": conflicts}
    if not chosen:
        return {"ok": False, "reason": "分析时点没有任何有效且已审核的名册"}

    companies, name_by_id = await _build_inputs(db, chosen)
    tol = settings.tolerance

    # ---------- 经济权益 ----------
    econ = solve_economic(
        companies, tol=float(tol),
        singular_threshold=settings.singular_threshold,
        condition_limit=settings.condition_limit,
    )

    run = models.AnalysisRun(
        as_of_date=as_of,
        status=models.RUN_DRAFT if econ.ok else models.RUN_FAILED,
        tolerance=Decimal(str(tol)),
        diagnostics=json.dumps({
            **econ.diagnostics,
            "sccs": econ.sccs,
            "closed_components": econ.closed_components,
            "skipped_documents": skipped,
        }, ensure_ascii=False, default=str),
        failure_reason=econ.failure_reason,
        created_by=user,
    )
    db.add(run)
    await db.flush()

    # 采用名册快照（历史报告保留原来源）
    for cid, doc in chosen.items():
        db.add(models.AnalysisRunDoc(
            run_id=run.id, document_id=doc.id,
            company_subject_id=cid, revision_id=doc.revision_id,
        ))

    # ---------- 经济结果落库（含方程贡献明细） ----------
    for cid, result in econ.company_results.items():
        if result["solve_status"] == "non_unique":
            db.add(models.EconomicInterest(
                run_id=run.id, company_subject_id=cid,
                holder_subject_id=None,
                holder_label=f"封闭持股：{result['unallocatable']:.6f} 无法唯一分配到外部主体",
                is_unidentified=False,
                final_share=Decimal(str(result["unallocatable"])),
                solve_status="non_unique",
                detail_json=json.dumps(result, ensure_ascii=False, default=str),
            ))
            continue
        for item in result["interests"]:
            db.add(models.EconomicInterest(
                run_id=run.id, company_subject_id=cid,
                holder_subject_id=item["holder_subject_id"],
                holder_label=item["holder_label"],
                is_unidentified=item["is_unidentified"],
                final_share=Decimal(str(item["final_share"])),
                solve_status="unique",
                detail_json=json.dumps(item, ensure_ascii=False, default=str),
            ))

    # ---------- 表决控制（经济求解失败也照常计算，但运行整体不出正式数） ----------
    manuals = list(await db.scalars(
        select(models.ManualControlAssertion).where(
            models.ManualControlAssertion.active.is_(True)
        )
    ))
    seeds = []
    for m in manuals:
        mrev = await db.get(models.Revision, m.revision_id)
        if not mrev or mrev.status != models.ST_APPROVED:
            continue  # 人工控制结论同样须审核后才进入分析
        controller = await db.get(models.Subject, m.controller_subject_id)
        seeds.append(ManualSeed(
            company_id=m.company_subject_id,
            controller_id=m.controller_subject_id,
            controller_label=controller.name if controller else f"主体#{m.controller_subject_id}",
            reason=m.reason, provenance=m.provenance,
        ))
    control = analyze_control(companies, seeds, tol=float(tol))
    for f in control.findings:
        db.add(models.ControlFinding(
            run_id=run.id,
            controlled_company_id=f["controlled_company_id"],
            controller_subject_id=f["controller_subject_id"],
            controller_label=f["controller_label"],
            basis=f["basis"],
            controlled_votes=Decimal(str(f["controlled_votes"])) if f["controlled_votes"] is not None else None,
            total_votes=Decimal(str(f["total_votes"])) if f["total_votes"] is not None else None,
            vote_ratio=Decimal(str(f["vote_ratio"])) if f["vote_ratio"] is not None else None,
            provenance=f["provenance"],
            detail_json=json.dumps(f, ensure_ascii=False, default=str),
        ))

    await db.flush()
    return {
        "ok": True,
        "run_id": run.id,
        "status": run.status,
        "economic_ok": econ.ok,
        "failure_reason": econ.failure_reason,
        "unstable_companies": econ.unstable_companies,
        "closed_components": econ.closed_components,
        "skipped": skipped,
    }
