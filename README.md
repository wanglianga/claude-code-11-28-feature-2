# 企业股权关系分析平台（虚构演示）

帮助集团分析员把各子公司股东名册汇成**可追溯的持有关系**，分别回答三件事：

1. **谁最终享有多少经济权益**——对有向持股图建立联立方程，从原始股份求解最终经济份额；
2. **哪些公司受同一方控制**——按表决权票数独立计算控制范围，严格超过半数才扩展；
3. **哪些结论因资料不足还不能确定**——完全封闭持股报告"不可唯一求解"，未知持有人保留独立份额。

> **免责声明**：平台内所有企业、自然人、机构股东及名册均为**虚构样本**，不连接任何工商网站，
> 不构成真实投资、税务或法律结论。统一社会信用代码、证件号、文号均为编造。

## 一、技术栈与架构

| 层 | 技术 | 说明 |
| --- | --- | --- |
| 前端 | React 18 + TypeScript + Vite | 经济权益与表决控制并列展示，可展开方程贡献与控制依据 |
| 后端 | Python 3.12 + FastAPI + SQLAlchemy 2（异步） | REST API，自带 OpenAPI 3.x / Swagger UI |
| 数据库 | PostgreSQL 18 | 修订、名册、分析快照全部可追溯 |
| 数值 | NumPy（SVD） | 联立方程求解、秩/条件数/残差诊断 |

```
frontend (nginx/Vite)  ──/api──▶  backend (FastAPI)  ──▶  PostgreSQL
```

核心算法纪律：

- **先 SCC 后联立**：Tarjan 找出互相持股的强连通分量；对每个公司建立
  `x[i,h] = b[i,h] + Σ_j A[i,j]·x[j,h]`，矩阵形式 `(I−A)X=B`，SVD 求解。
- **封闭不近似**：完全封闭、无法分配到外部主体的 SCC 报告 `non_unique`，
  **不限制遍历深度、不给近似百分比**。
- **未知不归一**：未知持有人是独立的 B 列，已知部分不会被重新归一成 100%。
- **数值不稳定即停**：秩亏损、条件数超限、残差或权益合计超容差时停止正式出数并列出公司。
- **控制另算**：以直接可行使票数为起点，只累计**已确认受控公司**的票数，
  严格 `>50%` 才扩展并迭代到不动点；环形持股无法靠"假定彼此受控"自举。
- **章程人工结论独立**：带出处的人工控制结论只影响控制视图，绝不修改经济份额。

## 二、一键启动（Docker Compose）

```bash
docker compose up --build
```

- 前端：http://localhost:8080
- 后端 API / Swagger UI：http://localhost:8000/docs
- PostgreSQL：localhost:5432（库/用户/密码 `ccdev`）
- 后端容器 `AUTO_SEED=1`，首次启动自动幂等播种全部虚构样本（含已审核修订）。

## 三、本地开发启动（使用容器预装运行时）

### 1. 后端

```bash
cd backend
/opt/python-3.12/bin/python3 -m venv ../.venv
../.venv/bin/pip install -r requirements.txt
# 预置 PostgreSQL：127.0.0.1:5432，库/用户/密码 ccdev
PYTHONPATH=. ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
# 另开终端播种虚构样本（幂等）：
PYTHONPATH=. ../.venv/bin/python -m app.seed_cli
```

### 2. 前端

```bash
cd frontend
/opt/node-22/bin/npm install --cache=.npm-cache
/opt/node-22/bin/npm run dev        # http://127.0.0.1:5173 ，/api 代理到 8000
```

### 3. 测试账号（角色分离）

| 账号 | 密码 | 角色 | 权限 |
| --- | --- | --- | --- |
| `analyst` | `analyst123` | 分析员 | 导入名册、处理同名合并、登记人工结论、发起分析 |
| `reviewer` | `reviewer123` | 审核员 | 审核修订（含总量校验）、发布报告 |
| `admin` | `admin123` | 兼两者 | 上述全部 + 一键播种 |

## 四、验证方式（本仓库实际执行过的验证）

### 4.1 后端测试：16 项全部通过

```bash
cd backend
# 测试使用独立库 ccdev_test：
PGPASSWORD=ccdev /opt/postgresql-18/bin/psql -h 127.0.0.1 -U ccdev -d ccdev \
  -c "CREATE DATABASE ccdev_test;"   # 仅首次需要
PYTHONPATH=. ../.venv/bin/python -m pytest tests/ -q
# 结果：16 passed
```

- `tests/test_solver.py`（8 项）：期望值全部由**纸笔手算独立推导**，不取自程序输出，
  含交叉持股、封闭 SCC、未知份额、票权分离、环形防自举、人工控制、SCC、严格过半。
- `tests/test_api.py`（8 项）：指纹去重、失败行修复续导、同名冲突禁并、
  时点名册唯一选取、更正使待发布结果过期且历史快照保留、总量不符拒审、撤回与 RBAC。

### 4.2 前端构建

```bash
cd frontend && /opt/node-22/bin/npm run build   # tsc 类型检查 + vite build 成功
```

### 4.3 端到端人工核对（HTTP 实测）

> 说明：当前隔离容器内**没有 Docker CLI**，故 `docker compose up` 未在本环境实际执行；
> `docker-compose.yml` 已通过 YAML 与服务依赖/健康检查结构校验，两个 Dockerfile 为标准
> Python-slim / Node+Nginx 多阶段构建。下列验证均使用题目预装的 Python 3.12 / Node 22 /
> PostgreSQL 18 运行时，以与容器内相同的启动命令完成。

播种后以分析时点 `2026-03-01` 发起分析，实测：

- 采用名册 **11** 份，每公司唯一一份有效已审核资料；
- SCC `[甲,乙]`（可解）与 `[丙,丁]`（封闭），封闭组件输出 `non_unique` 且无外部百分比；
- 甲集团手算值复现（见下节）；壬实业未知份额独立保留 40%；
- 戊科技王辅庭以 86.96% 票数直接控制，控制经 F→G→H 两轮扩展；
- 丑公司同时出现张东明直接控制（80% 票）与周衡之章程人工控制，互不覆盖；
- 审核员发布成功，分析员发布返回 403。

## 五、四个演示场景与手算样本

### 场景 1：交叉持股可求解（甲 A / 乙 B）

- A：张东明 600、B 持 400（总量 1000）；B：A 持 200、晨星 800（总量 1000）。
- 联立：`I−A = [[1,−0.4],[−0.2,1]]`，行列式 `0.92`。
- 张东明在 A 的最终份额 `0.6/0.92 = 0.65217391`；晨星在 B 为 `0.8/0.92 = 0.86956522`。
- 测试 `test_cross_holding_solvable_by_hand` 逐项断言，页面可点开"方程贡献"查看 A_ij·x_jh。

### 场景 2：完全封闭不可确定（丙 C / 丁 D）

- C：D 持 60%、自持 40%；D：C 持 100%。无任何外部渗入。
- 系统判定封闭 SCC，输出 `non_unique` 与不可分配份额 1.0，**不产生近似百分比**
  （`test_closed_loop_non_unique`）。

### 场景 3：股权与票权分离（戊 F → 己 G → 庚 H）

- F：普通股 6000（一股一票）+ 优先股 4000（一股十票），总票数 46000。
- 王辅庭经济权益仅 40%，却持 40000 票（86.96%）直接控制 F；
  F 控制 G（51%），G 控制 H（60%），控制经两轮不动点扩展到 H
  （`test_control_vote_equity_separation_and_expansion`）。

### 场景 4：增资更正（辛 K）

- 增资前 V1：总量 1000 股，有效期至 2025-06-30；增资后 V2：总量 2000 股，更正 V1。
- 时点 `2025-03-15` 只取 V1，时点 `2026-03-01` 只取 V2，**两份绝不拼成一次分析**；
- V2 导入后，引用 V1 的待发布运行立即置 `stale` 且禁止发布，历史报告保留 V1 来源快照
  （`test_correction_expires_draft_run_but_history_kept`）。

另有：寅制造 R 含一行负股份 → `partial_failed`，修正后从断点续导成功才可审核；
癸商贸 O 两个同名"恒信顾问"标识不一致 → 冲突候选，人工确认也被 409 拒绝。

## 六、主要 API（详见 /docs 的 OpenAPI）

| 模块 | 端点 |
| --- | --- |
| 认证 | `POST /api/auth/login`、`GET /api/auth/me` |
| 主体/匹配 | `GET/POST /api/subjects`、`GET /api/merge-candidates`、`POST /api/merge-candidates/{id}/decision` |
| 名册 | `POST /api/rosters/import`、`POST /api/rosters/resume/{source_ref}`、`POST /api/rosters/rows/{id}/repair`、`GET /api/rosters/documents` |
| 审核 | `GET /api/revisions`、`POST /api/revisions/{id}/review`、`POST /api/manual-controls` |
| 分析 | `POST /api/analysis/runs`、`GET /api/analysis/runs/{id}`、`POST /api/analysis/runs/{id}/publish` |
| 管理 | `POST /api/admin/seed` |

## 七、关键业务规则落点

- 每份名册带公司标识、股本类别、股份数、每股表决权、有效区间、出处（见导入模板）。
- 同名仅生成候选；统一社会标识不一致禁止自动归并，冲突候选由后端强制拦截。
- 审核时强制"同类别股份合计 = 登记总量"，不符即驳回；失败行未续导完成不得审核。
- 导入按 `source_ref + SHA-256 内容指纹`识别重复/更正/撤回，事件全程留痕。
- 所有分析结果挂在修订与名册快照上，可追溯到原始出处；更正只使**待发布**结果过期，
  已发布历史报告只读保留。
- 容差与数值阈值可通过环境变量调整：`EQUATION_TOLERANCE`、`SINGULAR_THRESHOLD`、`CONDITION_LIMIT`。
