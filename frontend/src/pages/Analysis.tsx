import { useEffect, useState } from 'react'
import { api, ApiError, detailText } from '../api'
import { StatusBadge, num, pct } from '../components/badges'
import { useAuth } from '../auth'

interface RunSummary {
  id: number
  as_of_date: string
  status: string
  failure_reason: string | null
  created_at: string
  published_at: string | null
}

interface Economic {
  company_subject_id: number
  company_name: string
  holder_subject_id: number | null
  holder_label: string
  is_unidentified: boolean
  final_share: string
  solve_status: string
  contributions: any[]
}

interface Control {
  controlled_company_id: number
  company_name: string
  controller_subject_id: number | null
  controller_label: string
  basis: string
  controlled_votes: string | null
  total_votes: string | null
  vote_ratio: string | null
  provenance: string | null
  expansion: any[]
}

interface RunDetail {
  id: number
  as_of_date: string
  status: string
  tolerance: string
  diagnostics: any
  failure_reason: string | null
  created_by: string
  docs: any[]
  economic: Economic[]
  controls: Control[]
}

export default function Analysis() {
  const { role } = useAuth()
  const canRun = role === 'analyst' || role === 'admin'
  const canPublish = role === 'reviewer' || role === 'admin'
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [asOf, setAsOf] = useState('2026-03-01')
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')

  async function loadRuns(selectId?: number) {
    const list = await api<RunSummary[]>('/analysis/runs')
    setRuns(list)
    if (selectId) await openRun(selectId)
    else if (list.length && !detail) await openRun(list[0].id)
  }
  useEffect(() => { loadRuns().catch((e) => setError(String(e))) }, [])

  async function openRun(id: number) {
    setDetail(await api<RunDetail>(`/analysis/runs/${id}`))
  }

  async function createRun() {
    setError(''); setInfo('')
    try {
      const r = await api<any>('/analysis/runs', {
        method: 'POST',
        body: JSON.stringify({ as_of_date: asOf }),
      })
      setInfo(`分析运行 #${r.run_id} 已创建（状态：${r.status}）`)
      await loadRuns(r.run_id)
    } catch (err) {
      setError(err instanceof ApiError ? detailText(err.detail) : String(err))
    }
  }

  async function publish(id: number) {
    setError(''); setInfo('')
    try {
      await api(`/analysis/runs/${id}/publish`, { method: 'POST' })
      setInfo(`运行 #${id} 已发布`)
      await loadRuns(id)
    } catch (err) {
      setError(err instanceof ApiError ? detailText(err.detail) : String(err))
    }
  }

  const companies: { id: number; name: string }[] = detail
    ? Array.from(new Map(detail.docs.map((d) => [d.company_subject_id, d.company_name])).entries())
        .map(([id, name]) => ({ id: id as number, name: name as string }))
    : []

  return (
    <div>
      <div className="page-head">
        <div>
          <h2>股权分析：经济权益与表决控制</h2>
          <div className="desc">联立方程求最终经济份额；表决控制按票数独立计算。结果从原始股份计算，不截断路径深度，不做近似归一</div>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {info && <div className="alert ok">{info}</div>}

      <div className="panel">
        <div className="panel-title">分析运行</div>
        <div className="panel-body">
          <div className="form-inline">
            {canRun && (
              <>
                <label className="field">
                  <span>分析时点</span>
                  <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
                </label>
                <button onClick={createRun}>按此时点计算</button>
              </>
            )}
            <label className="field" style={{ minWidth: 260 }}>
              <span>历史运行（只读保留原来源）</span>
              <select value={detail?.id ?? ''} onChange={(e) => openRun(Number(e.target.value))}>
                {runs.map((r) => (
                  <option key={r.id} value={r.id}>
                    #{r.id} {r.as_of_date} · {r.status}
                  </option>
                ))}
              </select>
            </label>
            {detail && canPublish && detail.status === 'draft' && (
              <button className="success" onClick={() => publish(detail.id)}>发布该运行</button>
            )}
          </div>
          <div className="pill-list" style={{ marginTop: 12 }}>
            {runs.map((r) => (
              <button key={r.id} className={`small ${detail?.id === r.id ? '' : 'ghost'}`} onClick={() => openRun(r.id)}>
                #{r.id} {r.as_of_date} <StatusBadge status={r.status} />
              </button>
            ))}
          </div>
        </div>
      </div>

      {detail && (
        <>
          <div className="grid-3">
            <div className="stat"><div className="num">{detail.docs.length}</div><div className="lbl">采用名册（每公司唯一一份有效已审核）</div></div>
            <div className="stat"><div className="num">{detail.diagnostics?.closed_components?.length ?? 0}</div><div className="lbl">封闭持股组件（不可唯一分配）</div></div>
            <div className="stat">
              <div className="num mono" style={{ fontSize: 16 }}>
                {detail.diagnostics?.condition_number
                  ? Number(detail.diagnostics.condition_number).toExponential(2)
                  : '—'}
              </div>
              <div className="lbl">系数矩阵条件数（最大残差 {detail.diagnostics?.max_residual != null ? Number(detail.diagnostics.max_residual).toExponential(2) : '—'}）</div>
            </div>
          </div>

          {detail.status === 'failed' && (
            <div className="alert error">
              数值不稳定或求解失败，已停止正式出数：{detail.failure_reason}
              {detail.diagnostics && JSON.stringify(detail.diagnostics)}
            </div>
          )}
          {detail.status === 'stale' && (
            <div className="alert warn">该待发布结果已因更正名册过期：{detail.failure_reason}。历史快照仍保留，但禁止发布，须重新计算。</div>
          )}
          {detail.diagnostics?.sccs?.length > 0 && (
            <div className="alert info">
              检出互相持股连通部分（SCC）：
              {detail.diagnostics.sccs.map((s: any, i: number) => ` [${s.join(', ')}]`).join('；')}
              {detail.diagnostics.closed_components?.length > 0 &&
                ` ｜ 完全封闭组件：${detail.diagnostics.closed_components.map((s: any) => `[${s.join(', ')}]`).join('；')}`}
            </div>
          )}

          <div className="panel">
            <div className="panel-title">本次采用的名册与修订快照</div>
            <div className="panel-body" style={{ padding: 0 }}>
              <table>
                <thead><tr><th>公司</th><th>来源标识</th><th>名册标题</th><th>有效区间</th><th>修订</th><th>内容指纹</th></tr></thead>
                <tbody>
                  {detail.docs.map((d) => (
                    <tr key={d.document_id}>
                      <td>{d.company_name}</td>
                      <td className="mono">{d.source_ref}</td>
                      <td>{d.doc_title}</td>
                      <td className="nowrap muted">{d.effective_from} 至 {d.effective_to ?? '至今'}</td>
                      <td>r{d.revision_id}</td>
                      <td className="mono muted">{d.fingerprint}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {companies.map((c) => {
            const econ = detail.economic.filter((e) => e.company_subject_id === c.id)
            const ctrls = detail.controls.filter((x) => x.controlled_company_id === c.id)
            const nonUnique = econ.some((e) => e.solve_status === 'non_unique')
            const unidentified = econ.filter((e) => e.is_unidentified)
            return (
              <div className="panel" key={c.id}>
                <div className="panel-title">
                  {c.name}
                  <div className="row-flex">
                    {nonUnique ? <StatusBadge status="non_unique" /> : <StatusBadge status="unique" />}
                    {unidentified.length > 0 && <span className="badge amber">含未识别份额</span>}
                  </div>
                </div>
                <div className="panel-body">
                  <div className="grid-2">
                    {/* 经济权益 */}
                    <div>
                      <h4 style={{ margin: '0 0 10px' }}>最终经济权益（联立方程）</h4>
                      {nonUnique ? (
                        <div className="alert warn">
                          完全封闭持股，权益无法分配到外部主体，不输出近似百分比。
                          {econ.map((e) => <div key={e.holder_label}>{e.holder_label}</div>)}
                        </div>
                      ) : (
                        <table>
                          <thead><tr><th>持有人</th><th style={{ width: 150 }}>最终份额</th><th></th></tr></thead>
                          <tbody>
                            {econ.map((e, i) => (
                              <tr key={i}>
                                <td>
                                  {e.holder_label}
                                  {e.is_unidentified && <span className="badge amber" style={{ marginLeft: 6 }}>未识别·独立份额</span>}
                                  <details className="sub">
                                    <summary>方程贡献</summary>
                                    <div className="sub-body">
                                      {e.contributions.length === 0 && <div className="muted">无间接贡献</div>}
                                      {e.contributions.map((ct, j) => (
                                        <div key={j} className="kv" style={{ marginBottom: 6 }}>
                                          {ct.kind === 'direct' ? (
                                            <>直接持有：<b>{pct(ct.fraction, 6)}</b></>
                                          ) : (
                                            <>
                                              经由 <b>{ct.holder_company_name}</b>：
                                              A_ij={pct(ct.a_ij, 6)} × x_jh={pct(ct.x_jh, 6)}
                                              = <b>{pct(ct.product, 6)}</b>
                                            </>
                                          )}
                                        </div>
                                      ))}
                                    </div>
                                  </details>
                                </td>
                                <td>
                                  <div className="share-bar"><i style={{ width: `${Math.min(100, Number(e.final_share) * 100)}%` }} /></div>
                                  <div className="mono right">{pct(e.final_share, 6)}</div>
                                </td>
                                <td className="nowrap muted">{num(Number(e.final_share) * 100, 4)} / 100</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </div>

                    {/* 表决控制 */}
                    <div>
                      <h4 style={{ margin: '0 0 10px' }}>表决控制（按票数，严格过半）</h4>
                      {ctrls.length === 0 ? (
                        <div className="alert info">无任何主体达到严格超过半数，环形持股未被自举为控制。</div>
                      ) : (
                        <table>
                          <thead><tr><th>控制人</th><th>依据</th><th>票数 / 比例</th><th></th></tr></thead>
                          <tbody>
                            {ctrls.map((ct, i) => (
                              <tr key={i}>
                                <td>{ct.controller_label}</td>
                                <td><StatusBadge status={ct.basis} /></td>
                                <td className="nowrap">
                                  {ct.vote_ratio === null ? '—' : (
                                    <>
                                      <div className="mono">{pct(ct.vote_ratio, 4)}</div>
                                      <div className="muted">{num(ct.controlled_votes)} / {num(ct.total_votes)} 票</div>
                                    </>
                                  )}
                                </td>
                                <td>
                                  <details className="sub">
                                    <summary>控制依据</summary>
                                    <div className="sub-body">
                                      {ct.provenance && <div className="kv">章程出处：<b>{ct.provenance}</b></div>}
                                      {ct.expansion.map((ex, j) => (
                                        <div key={j} className="kv" style={{ marginBottom: 8 }}>
                                          {ex.round !== undefined && <div>扩展轮次：第 {ex.round} 轮 · {ex.rule ?? ''}</div>}
                                          {ex.direct_votes !== undefined && (
                                            <div>直接票数：{num(ex.direct_votes)}；受控公司票数合计：{num(ex.controlled_part_sum)}</div>
                                          )}
                                          {ex.controlled_company_votes?.map((cv: any, k: number) => (
                                            <div key={k}>└ 已受控 {cv.controlled_company_name} 支配 {num(cv.votes)} 票</div>
                                          ))}
                                          {ex.reason && <div>理由：{ex.reason}</div>}
                                          {ex.provenance && <div>出处：{ex.provenance}</div>}
                                        </div>
                                      ))}
                                    </div>
                                  </details>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )
          })}
        </>
      )}
    </div>
  )
}
