// API 封装：令牌存 localStorage，401 时清理并回到登录页。
export const TOKEN_KEY = 'equity_token'
export const ROLE_KEY = 'equity_role'
export const NAME_KEY = 'equity_user'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  detail: any
  constructor(status: number, detail: any) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail))
    this.status = status
    this.detail = detail
  }
}

export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> | undefined),
  }
  if (options.body && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`/api${path}`, { ...options, headers })
  const text = await res.text()
  const data = text ? JSON.parse(text) : null
  if (!res.ok) {
    throw new ApiError(res.status, data?.detail ?? data ?? res.statusText)
  }
  return data as T
}

export function detailText(detail: any): string {
  if (detail == null) return '未知错误'
  if (typeof detail === 'string') return detail
  if (detail.reason) return detail.reason
  if (Array.isArray(detail.conflicts)) {
    return detail.conflicts.map((c: any) => c.reason).join('；')
  }
  if (detail.class_problems) {
    return (
      '股份类别合计与登记总量不符：' +
      detail.class_problems
        .map((p: any) => `${p.share_class}（登记 ${p.registered_total}，合计 ${p.holder_sum}）`)
        .join('；')
    )
  }
  return JSON.stringify(detail, null, 2)
}
