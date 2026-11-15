import { useEffect, useState } from 'react'
import { api, ApiError, detailText } from '../api'
import { StatusBadge } from '../components/badges'
import { useAuth } from '../auth'

interface Revision {
  id: number
  entity_type: string
  entity_id: number
  revision_no: number
  status: string
  change_note: string
  source_doc_id: number | null
  created_by: string
  reviewed_by: string | null
  created_at: string
}

interface Subject {
  id: number
  name: string
  subject_type: string
}

interface Manual {
  id: number
  company_subject_id: number
  controller_subject_id: number
  reason: string
  provenance: string
  active: boolean
  revision_id: number
}

const ENTITY_TEXT: Record<string, string> = {
  subject: '主体', roster_document: '名册文档', roster_row: '名册行',
  subject_merge: '主体合并', manual_control: '人工控制结论',
}

export default function Review() {
  const { role } = useAuth()
  const canReview = role === 'reviewer' || role === 'admin'
  const canEdit = role === 'analyst' || role === 'admin'
  const [revisions, setRevisions] = useState<Revision[]>([])
  const [subjects, setSubjects] = useState<Subject[]>([])
  const [manuals, setManuals] = useState<Manual[]>([])
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [form, setForm] = useState({ company_subject_id: '', controller_subject_id: '', reason: '', provenance: '' })

  async function load() {
    setRevisions(await api('/revisions'))
    setSubjects(await api('/subjects'))
    setManuals(await api('/manual-controls'))
  }
  useEffect(() => { load().catch((e) => setError(String(e))) }, [])

  const nameOf = (id: number) => subjects.find((s) => s.id === id)?.name ?? `#${id}`

  async function review(id: number, approve: boolean) {
    setError(''); setInfo('')
    try {
      await api(`/revisions/${id}/review`, {
        method: 'POST',
        body: JSON.stringify({ approve, note: approve ? '审核通过' : '资料存疑，驳回' }),
      })
      setInfo(`修订 #${id} 已${approve ? '通过' : '驳回'}`)
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? detailText(err.detail) : String(err))
    }
  }

  async function addManual(e: React.FormEvent) {
    e.preventDefault()
    setError(''); setInfo('')
    try {
      await api('/manual-controls', {
        method: 'POST',
        body: JSON.stringify({
          ...form,
          company_subject_id: Number(form.company_subject_id),
          controller_subject_id: Number(form.controller_subject_id),
        }),
      })
      setInfo('人工控制结论已登记为草稿修订，须审核通过后才进入分析；经济份额不受影响')
      setForm({ company_subject_id: '', controller_subject_id: '', reason: '', provenance: '' })
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? detailText(err.detail) : String(err))
    }
  }

  const pending = revisions.filter((r) => ['draft', 'in_review'].includes(r.status))

  return (
    <div>
      <div className="page-head">
        <div>
          <h2>修订审核</h2>
          <div className="desc">分析只采用已审核修订；名册审核时强制校验同类别股份合计等于登记总量，失败行未续导完成不得通过</div>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {info && <div className="alert ok">{info}</div>}

      <div className="panel">
        <div className="panel-title">待处理修订（{pending.length}）</div>
        <div className="panel-body" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>#</th><th>对象</th><th>修订号</th><th>说明</th><th>创建人</th><th>状态</th><th>操作</th></tr></thead>
            <tbody>
              {pending.map((r) => (
                <tr key={r.id}>
                  <td>{r.id}</td>
                  <td>{ENTITY_TEXT[r.entity_type] ?? r.entity_type} #{r.entity_id}</td>
                  <td>r{r.revision_no}</td>
                  <td style={{ maxWidth: 360 }}>{r.change_note}</td>
                  <td>{r.created_by}</td>
                  <td><StatusBadge status={r.status} /></td>
                  <td>
                    {canReview ? (
                      <div className="row-flex">
                        <button className="small success" onClick={() => review(r.id, true)}>通过</button>
                        <button className="small danger" onClick={() => review(r.id, false)}>驳回</button>
                      </div>
                    ) : <span className="muted">仅审核员可操作</span>}
                  </td>
                </tr>
              ))}
              {pending.length === 0 && <tr><td colSpan={7} className="muted" style={{ padding: 18 }}>暂无待处理修订</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">全部修订记录（最近 200 条）</div>
        <div className="panel-body" style={{ padding: 0, maxHeight: 320, overflow: 'auto' }}>
          <table>
            <thead><tr><th>#</th><th>对象</th><th>修订号</th><th>状态</th><th>审核人</th><th>说明</th></tr></thead>
            <tbody>
              {revisions.map((r) => (
                <tr key={r.id}>
                  <td>{r.id}</td>
                  <td className="nowrap">{ENTITY_TEXT[r.entity_type] ?? r.entity_type} #{r.entity_id}</td>
                  <td>r{r.revision_no}</td>
                  <td><StatusBadge status={r.status} /></td>
                  <td>{r.reviewed_by ?? '—'}</td>
                  <td className="muted" style={{ maxWidth: 420 }}>{r.change_note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid-2">
        <div className="panel">
          <div className="panel-title">章程人工控制结论（带出处，独立于经济计算）</div>
          <div className="panel-body" style={{ padding: 0 }}>
            <table>
              <thead><tr><th>公司</th><th>控制人</th><th>依据与出处</th><th>修订</th></tr></thead>
              <tbody>
                {manuals.map((m) => (
                  <tr key={m.id}>
                    <td>{nameOf(m.company_subject_id)}</td>
                    <td>{nameOf(m.controller_subject_id)}</td>
                    <td style={{ maxWidth: 300 }}>
                      {m.reason}
                      <div className="muted">出处：{m.provenance}</div>
                    </td>
                    <td>#{m.revision_id}</td>
                  </tr>
                ))}
                {manuals.length === 0 && <tr><td colSpan={4} className="muted" style={{ padding: 16 }}>暂无人工结论</td></tr>}
              </tbody>
            </table>
          </div>
        </div>

        {canEdit && (
          <div className="panel">
            <div className="panel-title">登记人工控制结论</div>
            <div className="panel-body">
              <form onSubmit={addManual}>
                <label className="field">
                  <span>公司</span>
                  <select value={form.company_subject_id}
                    onChange={(e) => setForm({ ...form, company_subject_id: e.target.value })} required>
                    <option value="">请选择</option>
                    {subjects.filter((s) => s.subject_type === 'company').map((s) => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>控制人主体</span>
                  <select value={form.controller_subject_id}
                    onChange={(e) => setForm({ ...form, controller_subject_id: e.target.value })} required>
                    <option value="">请选择</option>
                    {subjects.map((s) => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>章程约定理由</span>
                  <textarea value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} required />
                </label>
                <label className="field">
                  <span>出处（文件 / 条款）</span>
                  <input value={form.provenance} onChange={(e) => setForm({ ...form, provenance: e.target.value })} required />
                </label>
                <button>登记（进入审核）</button>
              </form>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
