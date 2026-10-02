/** 统一请求封装：拼后端地址、带操作人身份头、抛网络错误、给页脚留一句可读的说明。 */
import { useSessionStore } from '@/stores/session'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export function request(path: string, init?: RequestInit): Promise<Response> {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`
  const session = useSessionStore()
  const headers = new Headers(init?.headers)
  headers.set('Content-Type', 'application/json')
  // 头部只能放拉丁字符，中文身份先编码再传，后端负责解回
  headers.set('X-Operator-Name', encodeURIComponent(session.operator))
  headers.set('X-Operator-Role', encodeURIComponent(session.role))
  headers.set('X-Operator-Site', encodeURIComponent(session.site))
  return fetch(url, { ...init, headers }).catch((error: unknown) => {
    const detail = error instanceof Error ? error.message : '请求未送达'
    throw new Error(`接口请求失败：${detail}`)
  })
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}，数据未更新`)
  }
  return (await response.json()) as T
}
