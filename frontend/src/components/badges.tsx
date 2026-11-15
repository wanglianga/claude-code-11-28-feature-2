const STATUS_STYLE: Record<string, string> = {
  draft: 'gray',
  in_review: 'amber',
  approved: 'green',
  rejected: 'red',
  superseded: 'violet',
  withdrawn: 'red',
  received: 'gray',
  processing: 'amber',
  partial_failed: 'red',
  imported: 'green',
  published: 'green',
  stale: 'amber',
  failed: 'red',
  pending: 'amber',
  ok: 'green',
  conflict: 'red',
  confirmed: 'green',
  active: 'blue',
  merged: 'gray',
  unique: 'green',
  non_unique: 'red',
  direct: 'blue',
  expanded: 'violet',
  manual: 'amber',
}

const STATUS_TEXT: Record<string, string> = {
  draft: '草稿', in_review: '审核中', approved: '已审核', rejected: '已驳回',
  superseded: '已被更正', withdrawn: '已撤回', received: '已接收',
  processing: '处理中', partial_failed: '部分失败', imported: '已导入',
  published: '已发布', stale: '已过期', failed: '求解失败',
  pending: '待确认', ok: '成功', conflict: '冲突', confirmed: '已确认',
  active: '在册', merged: '已合并', unique: '唯一解', non_unique: '不可唯一',
  direct: '直接控制', expanded: '扩展控制', manual: '章程人工',
}

export function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_STYLE[status] ?? 'gray'
  return <span className={`badge ${cls}`}>{STATUS_TEXT[status] ?? status}</span>
}

export function pct(x: string | number | null | undefined, digits = 4): string {
  if (x === null || x === undefined || x === '') return '—'
  const v = typeof x === 'number' ? x : Number(x)
  if (!Number.isFinite(v)) return String(x)
  return `${(v * 100).toFixed(digits)}%`
}

export function num(x: string | number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || x === '') return '—'
  const v = typeof x === 'number' ? x : Number(x)
  if (!Number.isFinite(v)) return String(x)
  return v.toLocaleString('zh-CN', { maximumFractionDigits: digits })
}
