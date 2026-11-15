import { Fragment, useEffect, useState } from 'react'
import { api, ApiError, detailText } from '../api'
import { StatusBadge } from '../components/badges'
import { useAuth } from '../auth'

interface Doc {
  id: number
  source_ref: string
  doc_title: string
  company_name: string
  effective_from: string
  effective_to: string | null
  process_status: string
  total_rows: number
  next_row_seq: number
  corrected_by_doc_id: number | null
  revision_status: string
}

interface Row {
  id: number
  row_seq: number
  holder_raw_name: string | null
  holder_type: string
  share_class: string
  shares: string
  votes_per_share: string
  is_total: boolean
  registered_total: string | null
  provenance: string
  process_status: string
  error_detail: string | null
}

interface ImportEvent {
  id: number
  source_ref: string
  content_fingerprint: string
  event_kind: string
  document_id: number | null
  succeeded_rows: number
  failed_rows: number
  message: string | null
  created_at: string
}

const TEMPLATE = JSON.stringify(
  {
    source_ref: 'SRC-DEMO-001',
    doc_title: '示例公司股东名册',
    company_name: '示例公司有限公司（虚构）',
    company_unified_code: '91310000MA1FLEX001',
    effective_from: '2026-01-01',
    effective_to: null,
    corrects_source_ref: null,
    withdrawal: false,
    rows: [
      {
        holder_name: null, holder_unified_code: null, holder_type: 'company',
        share_class: 'common', shares: 1000, votes_per_share: 1,
        is_total: true, registered_total: 1000,
        effective_from: '2026-01-01', effective_to: null, provenance: '登记总量页',
      },
      {
        holder_name: '张东明', holder_unified_code: '31010119900101001X', holder_type: 'person',
        share_class: 'common', shares: 600, votes_per_share: 1,
        is_total: false, registered_total: null,
        effective_from: '2026-01-01', effective_to: null, provenance: '名册第1页',
      },
      {
        holder_name: '未知持有人', holder_unified_code: null, holder_type: 'person',
        share_class: 'common', shares: 400, votes_per_share: 1,
        is_total: false, registered_total: null,
        effective_from: '2026-01-01', effective_to: null, provenance: '名册第2页',
      },
    ],
  },
  null,
  2,
)

export default function Rosters() {
  const { role } = useAuth()
  const canEdit = role === 'analyst' || role === 'admin'
  const [docs, setDocs] = useState<Doc[]>([])
  const [events, setEvents] = useState<ImportEvent[]>([])
  const [payload, setPayload] = useState(TEMPLATE)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [openDoc, setOpenDoc] = useState<number | null>(null)
  const [rows, setRows] = useState<Row[]>([])
  const [repairShares, setRepairShares] = useState<Record<number, string>>({})

  async function load() {
    setDocs(await api('/rosters/documents'))
    setEvents(await api('/rosters/import-events'))
  }
  useEffect(() => { load().catch((e) => setError(String(e))) }, [])

  async function seed() {
    setError(''); setInfo('')
    try {
      const r = await api<{ auto_approved: number; imported: any[]; skipped: any[] }>(
        '/admin/seed', { method: 'POST' })
      setInfo(`播种完成：导入 ${r.imported.length} 份，跳过 ${r.skipped.length} 份，自动审核 ${r.auto_approved} 份`)
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? detailText(err.detail) : String(err))
    }
  }

  async function doImport(withdraw = false) {
    setError(''); setInfo('')
    try {
      const body = JSON.parse(payload)
      if (withdraw) body.withdrawal = true
      const r = await api<any>('/rosters/import', { method: 'POST', body: JSON.stringify(body) })
      setInfo(`${r.message}｜成功 ${r.succeeded_rows} 行，失败 ${r.failed_rows} 行，状态：${r.process_status ?? r.event_kind}`)
      await load()
    } catch (err) {
      if (err instanceof SyntaxError) setError('JSON 格式错误：' + err.message)
      else setError(err instanceof ApiError ? detailText(err.detail) : String(err))
    }
  }

  async function toggleRows(docId: number) {
    if (openDoc === docId) { setOpenDoc(null); return }
    setOpenDoc(docId)
    setRows(await api(`/rosters/documents/${docId}/rows`))
  }

  async function repair(row: Row) {
    const shares = repairShares[row.id]
    if (!shares) { setError('请先填写修正后的股份数'); return }
    setError('')
    try {
      await api(`/rosters/rows/${row.id}/repair`, {
        method: 'POST',
        body: JSON.stringify({ shares }),
      })
      setInfo(`行 ${row.row_seq} 已重置为待处理，请执行续导`)
      setRows(await api(`/rosters/documents/${openDoc}/rows`))
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? detailText(err.detail) : String(err))
    }
  }

  async function resume(sourceRef: string) {
    setError(''); setInfo('')
    try {
      const r = await api<any>(`/rosters/resume/${encodeURIComponent(sourceRef)}`, { method: 'POST' })
      setInfo(`${r.message}｜状态：${r.process_status}`)
      if (openDoc) setRows(await api(`/rosters/documents/${openDoc}/rows`))
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? detailText(err.detail) : String(err))
    }
  }

  return (
    <div>
      <div className="page-head">
        <div>
          <h2>名册导入与追溯</h2>
          <div className="desc">按来源标识 + 内容指纹识别重复 / 更正 / 撤回；部分失败可从首个未处理行续导，整份名册不算成功</div>
        </div>
        {role === 'admin' && <button onClick={seed}>一键播种虚构样本</button>}
      </div>

      {error && <div className="alert error">{error}</div>}
      {info && <div className="alert ok">{info}</div>}
      <div className="disclaimer">
        所有企业、自然人、机构均为虚构样本，不连接工商网站，不作真实投资或法律结论。
        每份名册须带公司标识、股本类别、股份数、每股表决权、有效区间与出处。
      </div>

      <div className="grid-2">
        <div className="panel">
          <div className="panel-title">
            导入名册（JSON）
            {canEdit && (
              <div className="row-flex">
                <button className="small ghost" onClick={() => setPayload(TEMPLATE)}>重置为模板</button>
                <button className="small" onClick={() => doImport(false)}>导入</button>
                <button className="small danger" onClick={() => doImport(true)}>按来源撤回</button>
              </div>
            )}
          </div>
          <div className="panel-body">
            <textarea
              className="mono" style={{ minHeight: 380, width: '100%' }}
              value={payload} onChange={(e) => setPayload(e.target.value)}
              disabled={!canEdit}
            />
          </div>
        </div>

        <div>
          <div className="panel">
            <div className="panel-title">名册文档（{docs.length}）</div>
            <div className="panel-body" style={{ padding: 0 }}>
              <table>
                <thead><tr><th>来源 / 标题</th><th>公司</th><th>有效区间</th><th>状态</th><th></th></tr></thead>
                <tbody>
                  {docs.map((d) => (
                    <Fragment key={d.id}>
                      <tr>
                        <td>
                          <b>{d.source_ref}</b>
                          <div className="muted">{d.doc_title}</div>
                          {d.corrected_by_doc_id && <div className="muted">已被文档 #{d.corrected_by_doc_id} 更正</div>}
                        </td>
                        <td>{d.company_name}</td>
                        <td className="nowrap muted">
                          {d.effective_from}<br />至 {d.effective_to ?? '至今'}
                        </td>
                        <td>
                          <StatusBadge status={d.process_status} />
                          <div style={{ marginTop: 4 }}><StatusBadge status={d.revision_status ?? 'draft'} /></div>
                        </td>
                        <td>
                          <div className="row-flex">
                            <button className="small ghost" onClick={() => toggleRows(d.id)}>
                              {openDoc === d.id ? '收起' : '查看行'}
                            </button>
                            {d.process_status === 'partial_failed' && canEdit && (
                              <button className="small" onClick={() => resume(d.source_ref)}>续导</button>
                            )}
                          </div>
                        </td>
                      </tr>
                      {openDoc === d.id && (
                        <tr key={`${d.id}-rows`}>
                          <td colSpan={5} style={{ background: '#f8fafc' }}>
                            <table>
                              <thead><tr><th>#</th><th>持有人/总量</th><th>类别</th><th>股份</th><th>票/股</th><th>出处</th><th>状态</th><th>修复</th></tr></thead>
                              <tbody>
                                {rows.map((r) => (
                                  <tr key={r.id}>
                                    <td>{r.row_seq}</td>
                                    <td>
                                      {r.is_total ? <span className="badge blue">总量 {r.registered_total}</span> : (r.holder_raw_name ?? '—')}
                                      {r.error_detail && <div className="alert error" style={{ margin: '4px 0 0', padding: '4px 8px' }}>{r.error_detail}</div>}
                                    </td>
                                    <td>{r.share_class}</td>
                                    <td className="mono">{r.shares}</td>
                                    <td>{r.votes_per_share}</td>
                                    <td className="muted" style={{ maxWidth: 160 }}>{r.provenance}</td>
                                    <td><StatusBadge status={r.process_status} /></td>
                                    <td>
                                      {r.process_status === 'failed' && canEdit && (
                                        <div className="row-flex">
                                          <input style={{ width: 90 }} placeholder="正确股份数"
                                            value={repairShares[r.id] ?? ''}
                                            onChange={(e) => setRepairShares({ ...repairShares, [r.id]: e.target.value })} />
                                          <button className="small success" onClick={() => repair(r)}>修正</button>
                                        </div>
                                      )}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="panel">
            <div className="panel-title">导入事件日志（指纹判定）</div>
            <div className="panel-body" style={{ padding: 0 }}>
              <table>
                <thead><tr><th>来源</th><th>类型</th><th>指纹</th><th>行(成/败)</th><th>说明</th></tr></thead>
                <tbody>
                  {events.map((e) => (
                    <tr key={e.id}>
                      <td className="nowrap">{e.source_ref}</td>
                      <td><StatusBadge status={e.event_kind === 'new' ? 'active' : e.event_kind === 'duplicate' ? 'merged' : e.event_kind === 'correction' ? 'superseded' : e.event_kind === 'withdrawal' ? 'withdrawn' : 'processing'} /></td>
                      <td className="mono muted">{e.content_fingerprint}</td>
                      <td>{e.succeeded_rows}/{e.failed_rows}</td>
                      <td className="muted">{e.message}</td>
                    </tr>
                  ))}
                  {events.length === 0 && <tr><td colSpan={5} className="muted" style={{ padding: 18 }}>暂无事件</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
