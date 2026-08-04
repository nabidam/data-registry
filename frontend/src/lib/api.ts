const BASE = import.meta.env.VITE_API_URL ?? '/api'

type Params = Record<string, string | number | boolean | undefined | null>

function url(path: string, params?: Params): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  }
  const qs = query.toString()
  return `${BASE}${path}${qs ? `?${qs}` : ''}`
}

async function request<T>(path: string, init?: RequestInit, params?: Params): Promise<T> {
  const res = await fetch(url(path, params), init)
  if (!res.ok) {
    const detail = await res.text()
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T)
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  get: <T>(path: string, params?: Params) => request<T>(path, undefined, params),
  post: <T>(path: string, body: unknown) => request<T>(path, json(body)),
  patch: <T>(path: string, body: unknown) => request<T>(path, { ...json(body), method: 'PATCH' }),
  del: (path: string) => request<void>(path, { method: 'DELETE' }),
  upload: <T>(path: string, form: FormData) => request<T>(path, { method: 'POST', body: form }),
  downloadUrl: (path: string) => `${BASE}${path}`,
}

export type Id = number
