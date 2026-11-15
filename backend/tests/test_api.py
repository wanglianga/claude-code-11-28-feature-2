"""API 集成测试：导入指纹、续导、更正过期、匹配冲突、时点分析与发布全流程。"""
import asyncio
from datetime import date

import httpx
import pytest

from app.main import app
from app.seed_data import build_payloads

BASE = "http://test"


def run(coro):
    return asyncio.run(coro)


async def aclient():
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url=BASE)


async def login(client, username, password):
    r = await client.post("/api/auth/login",
                          json={"username": username, "password": password})
    assert r.status_code == 200
    return r.json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.usefixtures("fresh_db")
def test_login_and_seed():
    async def scenario():
        async with await aclient() as client:
            token = await login(client, "admin", "admin123")
            r = await client.post("/api/admin/seed", headers=auth(token))
            assert r.status_code == 200
            summary = r.json()
            # 13 份样本名册，寅制造 R 部分失败，其余完整
            refs = [x["source_ref"] for x in summary["imported"]]
            assert "SRC-R-2026" in refs and len(refs) == 13
            r_status = next(x for x in summary["imported"]
                            if x["source_ref"] == "SRC-R-2026")
            assert r_status["status"] == "partial_failed"
            assert r_status["failed_rows"] == 1
            # 完整名册被自动审核
            assert summary["auto_approved"] == 12
    run(scenario())


@pytest.mark.usefixtures("fresh_db")
def test_duplicate_fingerprint_ignored():
    async def scenario():
        async with await aclient() as client:
            token = await login(client, "analyst", "analyst123")
            payload = build_payloads()[0].model_dump(mode="json")
            r1 = await client.post("/api/rosters/import", json=payload, headers=auth(token))
            assert r1.json()["event_kind"] == "new"
            r2 = await client.post("/api/rosters/import", json=payload, headers=auth(token))
            body = r2.json()
            assert body["duplicate"] is True
            assert body["event_kind"] == "duplicate"
    run(scenario())


@pytest.mark.usefixtures("fresh_db")
def test_repair_and_resume_partial_roster():
    async def scenario():
        async with await aclient() as client:
            admin_t = await login(client, "admin", "admin123")
            await client.post("/api/admin/seed", headers=auth(admin_t))
            analyst_t = await login(client, "analyst", "analyst123")
            docs = (await client.get("/api/rosters/documents",
                                     headers=auth(analyst_t))).json()
            r_doc = next(d for d in docs if d["source_ref"] == "SRC-R-2026")
            assert r_doc["process_status"] == "partial_failed"
            rows = (await client.get(
                f"/api/rosters/documents/{r_doc['id']}/rows",
                headers=auth(analyst_t))).json()
            failed = next(r for r in rows if r["process_status"] == "failed")
            # 修正为正确的 50 股（550+50+400=1000）
            r = await client.post(
                f"/api/rosters/rows/{failed['id']}/repair",
                json={"shares": "50"}, headers=auth(analyst_t))
            assert r.status_code == 200
            r = await client.post("/api/rosters/resume/SRC-R-2026",
                                  headers=auth(analyst_t))
            body = r.json()
            assert body["process_status"] == "imported"
            assert body["failed_rows"] == 0
            # 整份名册此前不算成功；续导完成后才可被审核
            docs = (await client.get("/api/rosters/documents",
                                     headers=auth(analyst_t))).json()
            r_doc = next(d for d in docs if d["source_ref"] == "SRC-R-2026")
            assert r_doc["process_status"] == "imported"
    run(scenario())


@pytest.mark.usefixtures("fresh_db")
def test_merge_candidate_conflict_blocks_merge():
    async def scenario():
        async with await aclient() as client:
            admin_t = await login(client, "admin", "admin123")
            await client.post("/api/admin/seed", headers=auth(admin_t))
            analyst_t = await login(client, "analyst", "analyst123")
            candidates = (await client.get("/api/merge-candidates",
                                           headers=auth(analyst_t))).json()
            # 癸商贸 O 的两个"恒信顾问"同名但标识不同 -> conflict，禁止归并
            conflict = [c for c in candidates if c["status"] == "conflict"]
            assert conflict, "应存在统一社会标识不一致的冲突候选"
            r = await client.post(
                f"/api/merge-candidates/{conflict[0]['id']}/decision",
                json={"confirm": True}, headers=auth(analyst_t))
            assert r.status_code == 409
            assert "标识" in r.json()["detail"]
            # 普通同名候选（昌隆贸易行，无标识）可以人工确认
            pending = [c for c in candidates if c["status"] == "pending"]
            assert pending
            r = await client.post(
                f"/api/merge-candidates/{pending[0]['id']}/decision",
                json={"confirm": True, "note": "人工核对为同一商行"},
                headers=auth(analyst_t))
            assert r.status_code == 200
            assert r.json()["status"] == "confirmed"
    run(scenario())


@pytest.mark.usefixtures("fresh_db")
def test_analysis_full_suite():
    async def scenario():
        async with await aclient() as client:
            admin_t = await login(client, "admin", "admin123")
            await client.post("/api/admin/seed", headers=auth(admin_t))
            analyst_t = await login(client, "analyst", "analyst123")

            # ---- 时点 2025-03-15：辛股份 K 仅增资前 V1（1000 股）有效 ----
            r = await client.post("/api/analysis/runs",
                                  json={"as_of_date": "2025-03-15"},
                                  headers=auth(analyst_t))
            assert r.status_code == 200, r.text
            early_id = r.json()["run_id"]
            early = (await client.get(f"/api/analysis/runs/{early_id}",
                                      headers=auth(analyst_t))).json()
            assert len(early["docs"]) == 1  # 2025 年只有 K-V1 一份有效名册
            assert early["docs"][0]["source_ref"] == "SRC-K-V1"

            # ---- 时点 2026-03-01：K 的 V1 已失效，唯一有效且已审核的是 V2 ----
            r = await client.post("/api/analysis/runs",
                                  json={"as_of_date": "2026-03-01"},
                                  headers=auth(analyst_t))
            assert r.status_code == 200, r.text
            run_id = r.json()["run_id"]
            detail = (await client.get(f"/api/analysis/runs/{run_id}",
                                       headers=auth(analyst_t))).json()
            k_doc = next(d for d in detail["docs"] if "辛股份" in d["company_name"])
            assert k_doc["source_ref"] == "SRC-K-V2"

            # 封闭 C/D：non_unique 且不产出外部百分比
            closed = [e for e in detail["economic"] if e["solve_status"] == "non_unique"]
            closed_companies = {e["company_name"] for e in closed}
            assert any("丙闭环" in n for n in closed_companies)
            assert any("丁闭环" in n for n in closed_companies)

            # S1 交叉持股手算值：张东明在甲集团 0.6/0.92
            a_econ = [e for e in detail["economic"]
                      if "甲集团" in e["company_name"]
                      and e["holder_label"] == "张东明"][0]
            assert abs(float(a_econ["final_share"]) - 0.6 / 0.92) < 1e-8
            # 方程贡献可展开
            assert any(c["kind"] == "via_company" for c in a_econ["contributions"])

            # 未知持有人份额独立保留 0.4，未被归一
            m_unknown = [e for e in detail["economic"]
                         if "壬实业" in e["company_name"]
                         and e["is_unidentified"]][0]
            assert abs(float(m_unknown["final_share"]) - 0.4) < 1e-10

            # 票权分离：王辅庭控制戊科技；控制经 F->G->H 扩展两轮
            controls = {(c["controller_label"], c["company_name"]): c
                        for c in detail["controls"]}
            f_ctrl = next(v for k, v in controls.items() if "戊科技" in k[1])
            assert f_ctrl["basis"] == "direct"
            # Numeric(12,8) 落库舍入，比较到 1e-7
            assert abs(float(f_ctrl["vote_ratio"]) - 40000 / 46000) < 1e-7
            h_ctrl = next(v for k, v in controls.items() if "庚物流" in k[1])
            assert h_ctrl["basis"] == "expanded"
            assert h_ctrl["expansion"][0]["round"] >= 2

            # 章程人工控制：丑公司 -> 周衡之，basis=manual 且带出处
            n_ctrl = next(c for c in detail["controls"] if "丑公司" in c["company_name"]
                          and c["controller_label"] == "周衡之")
            assert n_ctrl["basis"] == "manual" and n_ctrl["provenance"]

            # ---- 时点 2026-09-01：K 只能采用增资后 V2（2000 股），不拼接 ----
            r = await client.post("/api/analysis/runs",
                                  json={"as_of_date": "2026-09-01"},
                                  headers=auth(analyst_t))
            run2 = r.json()["run_id"]
            detail2 = (await client.get(f"/api/analysis/runs/{run2}",
                                        headers=auth(analyst_t))).json()
            k_doc2 = next(d for d in detail2["docs"] if "辛股份" in d["company_name"])
            assert k_doc2["source_ref"] == "SRC-K-V2"
            zhao = [e for e in detail2["economic"]
                    if "辛股份" in e["company_name"]
                    and e["holder_label"] == "赵启年"][0]
            assert abs(float(zhao["final_share"]) - 0.6) < 1e-10  # 1200/2000
    run(scenario())


@pytest.mark.usefixtures("fresh_db")
def test_correction_expires_draft_run_but_history_kept():
    async def scenario():
        async with await aclient() as client:
            analyst_t = await login(client, "analyst", "analyst123")
            # 只导入增资前 V1 并审核
            payloads = build_payloads()
            v1 = next(p for p in payloads if p.source_ref == "SRC-K-V1")
            await client.post("/api/rosters/import",
                              json=v1.model_dump(mode="json"), headers=auth(analyst_t))
            docs = (await client.get("/api/rosters/documents",
                                     headers=auth(analyst_t))).json()
            rev_id = docs[0]["revision_id"]
            reviewer_t = await login(client, "reviewer", "reviewer123")
            await client.post(f"/api/revisions/{rev_id}/review",
                              json={"approve": True}, headers=auth(reviewer_t))
            r = await client.post("/api/analysis/runs",
                                  json={"as_of_date": "2025-03-01"},
                                  headers=auth(analyst_t))
            run_id = r.json()["run_id"]
            # 更正名册 V2 到达
            v2 = next(p for p in payloads if p.source_ref == "SRC-K-V2")
            await client.post("/api/rosters/import",
                              json=v2.model_dump(mode="json"), headers=auth(analyst_t))
            detail = (await client.get(f"/api/analysis/runs/{run_id}",
                                       headers=auth(analyst_t))).json()
            assert detail["status"] == "stale"
            assert "过期" in detail["failure_reason"]
            # 历史运行仍保留原来源快照
            assert detail["docs"][0]["source_ref"] == "SRC-K-V1"
            # 过期运行禁止发布
            r = await client.post(f"/api/analysis/runs/{run_id}/publish",
                                  headers=auth(reviewer_t))
            assert r.status_code == 409
    run(scenario())


@pytest.mark.usefixtures("fresh_db")
def test_class_total_mismatch_blocks_review():
    async def scenario():
        async with await aclient() as client:
            analyst_t = await login(client, "analyst", "analyst123")
            reviewer_t = await login(client, "reviewer", "reviewer123")
            from app.schemas import RosterImportIn, RosterRowIn
            bad = RosterImportIn(
                source_ref="SRC-BAD-1", doc_title="对不上总量的名册",
                company_name="测试公司（虚构）", company_unified_code="91310000MA1FL9999X",
                effective_from=date(2026, 1, 1), effective_to=None,
                rows=[
                    RosterRowIn(share_class="common", shares=1000, is_total=True,
                                registered_total=1000,
                                effective_from=date(2026, 1, 1), provenance="总量页"),
                    RosterRowIn(holder_name="张三", holder_type="person",
                                share_class="common", shares=600,
                                effective_from=date(2026, 1, 1), provenance="名册页"),
                ]).model_dump(mode="json")
            await client.post("/api/rosters/import", json=bad, headers=auth(analyst_t))
            docs = (await client.get("/api/rosters/documents",
                                     headers=auth(analyst_t))).json()
            rev_id = docs[0]["revision_id"]
            r = await client.post(f"/api/revisions/{rev_id}/review",
                                  json={"approve": True}, headers=auth(reviewer_t))
            assert r.status_code == 409
            assert "总量" in r.json()["detail"]["reason"]
    run(scenario())


@pytest.mark.usefixtures("fresh_db")
def test_withdrawal_and_rbac():
    async def scenario():
        async with await aclient() as client:
            analyst_t = await login(client, "analyst", "analyst123")
            payload = build_payloads()[0].model_dump(mode="json")
            await client.post("/api/rosters/import", json=payload, headers=auth(analyst_t))
            docs = (await client.get("/api/rosters/documents",
                                     headers=auth(analyst_t))).json()
            # analyst 无权审核
            r = await client.post(f"/api/revisions/{docs[0]['revision_id']}/review",
                                  json={"approve": True}, headers=auth(analyst_t))
            assert r.status_code == 403
            # 未认证拒绝
            r = await client.get("/api/subjects")
            assert r.status_code == 401
            # 撤回
            withdrawal = {**payload, "withdrawal": True, "rows": []}
            r = await client.post("/api/rosters/import",
                                  json=withdrawal, headers=auth(analyst_t))
            assert r.json()["event_kind"] == "withdrawal"
            docs = (await client.get("/api/rosters/documents",
                                     headers=auth(analyst_t))).json()
            assert docs[0]["process_status"] == "withdrawn"
    run(scenario())
