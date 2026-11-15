"""财务事实层 API 集成测试：来源穿透、回单冲突、拆分会聚、汇率缺失、
重述不回写审计快照、封存只读、跨期对齐、权属待证。"""
import asyncio

import httpx
import pytest

from app.main import app

BASE = "http://test"


def run(coro):
    return asyncio.run(coro)


async def aclient():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


async def login(client, username, password):
    r = await client.post("/api/auth/login",
                          json={"username": username, "password": password})
    return r.json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _seeded_admin(client):
    admin = await login(client, "admin", "admin123")
    await client.post("/api/admin/seed", headers=auth(admin))
    return admin


@pytest.mark.usefixtures("fresh_db")
def test_finance_seed_and_reconciliation_acceptance():
    async def scenario():
        async with await aclient() as client:
            admin = await _seeded_admin(client)
            h = auth(admin)
            # 播种覆盖验收样例
            stmts = (await client.get("/api/finance/profit-statements", headers=h)).json()
            assert len(stmts) == 7
            assert any(s["staging"] == "restated" for s in stmts)
            # 原始辛股份表被置 superseded 但保留原值
            orig = next(s for s in stmts if s["source_ref"] == "PS-K-FY2025-ORIG")
            assert orig["status"] == "superseded"
            assert float(orig["total_positive"]) == 350.0

            r = await client.post("/api/finance/reconciliation-runs",
                                  json={"as_of_date": "2026-06-30"}, headers=h)
            assert r.status_code == 200, r.text
            rid = r.json()["run_id"]
            d = (await client.get(f"/api/finance/reconciliation-runs/{rid}",
                                  headers=h)).json()

            # ---- C1 交叉持股残差：应收 AR-B-2026-01 = 32 + 6.4 + 1.6 ----
            recv1 = next(x for x in d["receivables"] if x["receivable_no"] == "AR-B-2026-01")
            c1 = [c for c in d["chains"] if c["receivable_id"] == recv1["id"]]
            by_kind = {}
            for c in c1:
                by_kind[c["entry_kind"]] = by_kind.get(c["entry_kind"], 0) + float(c["amount"])
            assert abs(by_kind["operating_line"] - 38.4) < 1e-6
            assert abs(by_kind["cycle_residual"] - 1.6) < 1e-6
            assert abs(sum(float(c["amount"]) for c in c1) - 40.0) < 1e-6
            # 残差链带理由，且不含循环重复累加
            resid = [c for c in c1 if c["entry_kind"] == "cycle_residual"][0]
            assert "不重复累加" in resid["detail"]["reason"]

            # ---- C8 权属待证：AR-A-2025-11 记录日无快照 ----
            recv_tp = next(x for x in d["receivables"] if x["receivable_no"] == "AR-A-2025-11")
            tp_chains = [c for c in d["chains"] if c["receivable_id"] == recv_tp["id"]]
            assert len(tp_chains) == 1 and tp_chains[0]["entry_kind"] == "title_pending"
            assert "权属待证" in tp_chains[0]["detail"]["reason"]
            assert any(p["receivable_no"] == "AR-A-2025-11"
                       for p in d["diagnostics"]["pending_titles"])

            # ---- C3 一对多冲突：两条 AR-H 应收均 one_to_many 且不核验 ----
            conflict = [m for m in d["matches"] if m["status"] == "one_to_many"]
            assert len(conflict) == 2
            assert all(m["verified"] is False for m in conflict)
            assert d["diagnostics"]["one_to_many_conflicts"]

            # ---- C4 拆分：AR-B-SPLIT-1201 两条 split ----
            split_recv = next(x for x in d["receivables"]
                              if x["receivable_no"] == "AR-B-SPLIT-1201")
            split = [m for m in d["matches"]
                     if m["receivable_id"] == split_recv["id"] and m["status"] == "split"]
            assert len(split) == 2
            assert sum(float(m["matched_amount"]) for m in split) == 120.0

            # ---- C5 汇聚：一张回单覆盖两条应收 ----
            agg = [m for m in d["matches"] if m["status"] == "aggregated"]
            assert len(agg) == 2
            assert len({m["receipt_id"] for m in agg}) == 1

            # ---- C7 汇率缺失：USD 只发原币、提示缺失；EUR 有获批汇率 ----
            usd = [b for b in d["bridges"]
                   if b["currency"] == "USD" and b["layer"] == "verified_receipt"][0]
            assert usd["fx_status"] == "missing"
            assert usd["reporting_amount"] is None
            eur_fx = [b for b in d["bridges"]
                      if b["layer"] == "fx_difference" and b["currency"] == "CNY"
                      and b["fx_status"] == "applied"]
            assert eur_fx and abs(float(eur_fx[0]["amount"]) - 0.5) < 1e-6

            # ---- C6 重述影响：辛股份 -10（60/300 × -50）----
            restate = [b for b in d["bridges"] if b["layer"] == "restatement_impact"]
            assert len(restate) == 1
            assert abs(float(restate[0]["amount"]) - (-10.0)) < 1e-6
            assert restate[0]["detail"]["original_statement_id"] != \
                restate[0]["detail"]["restated_statement_id"]

            # ---- 跨期对齐：AR-F 确认 FY2025，到账 2026-H1 ----
            tl = (await client.get("/api/finance/timeline", headers=h)).json()
            cross = [c for c in tl["cross_period_facts"]
                     if c["receivable_no"] == "AR-F-2025-12-001"]
            assert cross and cross[0]["receivable_period"] == "2025-FY"
            assert cross[0]["receipt_period"] == "2026-H1"

            # ---- 分层发布：五个层都在 ----
            layers = {b["layer"] for b in d["bridges"]}
            assert {"declared_receivable", "verified_receipt", "fx_difference",
                    "restatement_impact", "unallocatable"} <= layers
    run(scenario())


@pytest.mark.usefixtures("fresh_db")
def test_restatement_does_not_rewrite_sealed_audit_snapshot():
    async def scenario():
        async with await aclient() as client:
            admin = await _seeded_admin(client)
            h = auth(admin)
            r = await client.post("/api/finance/reconciliation-runs",
                                  json={"as_of_date": "2026-06-30"}, headers=h)
            rid = r.json()["run_id"]
            seal = await client.post(f"/api/finance/reconciliation-runs/{rid}/seal",
                                     headers=h)
            assert seal.status_code == 200
            seal_hash = seal.json()["seal_hash"]
            sealed_view = (await client.get(f"/api/finance/reconciliation-runs/{rid}",
                                            headers=h)).json()
            frozen_before = sealed_view["audit_snapshot"]["frozen"]

            # 重复封存被拒（只读）
            again = await client.post(f"/api/finance/reconciliation-runs/{rid}/seal",
                                      headers=h)
            assert again.status_code == 409

            # 封存后再次读取，已封存快照内容与哈希不变（重述不回写）
            after = (await client.get(f"/api/finance/reconciliation-runs/{rid}",
                                      headers=h)).json()
            assert after["audit_snapshot"]["seal_hash"] == seal_hash
            assert after["audit_snapshot"]["frozen"] == frozen_before

            # 真正制造一次封存后重述：新导入并审批一份辛股份重述表
            stmts = (await client.get("/api/finance/profit-statements", headers=h)).json()
            k_orig = next(s for s in stmts if s["source_ref"] == "PS-K-FY2025-ORIG")
            new_body = {
                "source_ref": "PS-K-FY2025-REST2", "company_subject_id": k_orig["company_subject_id"],
                "period_code": "2025-FY", "period_start": "2025-01-01", "period_end": "2025-12-31",
                "staging": "restated", "restates_source_ref": "PS-K-FY2025-ORIG",
                "lines": [
                    {"line_no": 1, "line_category": "operating",
                     "label": "辛股份经营利润（二次重述）", "amount": "200"},
                    {"line_no": 2, "line_category": "other",
                     "label": "辛股份其他收益（二次重述）", "amount": "50"},
                ],
            }
            r = await client.post("/api/finance/profit-statements", json=new_body, headers=h)
            assert r.status_code == 200, r.text
            new_id = r.json()["statement_id"]
            r = await client.post(f"/api/finance/profit-statements/{new_id}/approve", headers=h)
            assert r.status_code == 200

            # 再次读取：旧运行审计快照的哈希与内容仍未改变
            after2 = (await client.get(f"/api/finance/reconciliation-runs/{rid}",
                                       headers=h)).json()
            assert after2["audit_snapshot"]["seal_hash"] == seal_hash
            assert after2["audit_snapshot"]["frozen"] == frozen_before
    run(scenario())


@pytest.mark.usefixtures("fresh_db")
def test_finance_rbac_and_validation():
    async def scenario():
        client = await aclient()
        async with client:
            analyst = await login(client, "analyst", "analyst123")
            reviewer = await login(client, "reviewer", "reviewer123")
            # 分析员不能审批利润表
            r = await client.post("/api/finance/profit-statements/1/approve",
                                  headers=auth(analyst))
            assert r.status_code == 403
            # 分析员不能封存审计快照
            r = await client.post("/api/finance/reconciliation-runs/999/seal",
                                  headers=auth(analyst))
            assert r.status_code == 403
            # 未认证拒绝
            r = await client.get("/api/finance/profit-statements")
            assert r.status_code == 401
            # 未获批汇率必须有 rate
            r = await client.post("/api/finance/fx-rates", json={
                "from_currency": "JPY", "to_currency": "CNY",
                "approved": True, "source_ref": "FX-JPY",
            }, headers=auth(reviewer))
            assert r.status_code == 409
    run(scenario())
