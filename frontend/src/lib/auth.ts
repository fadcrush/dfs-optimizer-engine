'use client'

export const ACCESS_TOKEN_KEY = 'dfs_edge_access_token'
export const TOKEN_TYPE_KEY = 'dfs_edge_token_type'
export const USER_DATA_KEY = 'dfs_edge_user'
export const AUTH_SESSION_EVENT = 'dfs_edge_auth_session_changed'
export const AUTH_DISABLED = process.env.NEXT_PUBLIC_DISABLE_AUTH === '1'

export type StoredUser = {
  id: string
  email: string
  full_name?: string | null
  tier?: string
  is_admin?: boolean
}

export function getAccessToken(): string | null {
  if (typeof window === 'undefined') return null
  const token = window.localStorage.getItem(ACCESS_TOKEN_KEY)
  return token && token.trim() ? token.trim() : null
}

export function getTokenType(): string {
  if (typeof window === 'undefined') return 'Bearer'
  const tokenType = window.localStorage.getItem(TOKEN_TYPE_KEY)
  return tokenType && tokenType.trim() ? tokenType.trim() : 'Bearer'
}

export function storeAuthSession(accessToken: string, tokenType = 'bearer') {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(ACCESS_TOKEN_KEY, accessToken)
  window.localStorage.setItem(TOKEN_TYPE_KEY, tokenType)
  window.dispatchEvent(new Event(AUTH_SESSION_EVENT))
}

export function storeUserData(user: StoredUser): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(USER_DATA_KEY, JSON.stringify(user))
  window.dispatchEvent(new Event(AUTH_SESSION_EVENT))
}

export function getStoredUser(): StoredUser | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(USER_DATA_KEY)
    return raw ? (JSON.parse(raw) as StoredUser) : null
  } catch {
    return null
  }
}

export function clearAuthSession() {
  if (typeof window === 'undefined') return
  window.localStorage.removeItem(ACCESS_TOKEN_KEY)
  window.localStorage.removeItem(TOKEN_TYPE_KEY)
  window.localStorage.removeItem(USER_DATA_KEY)
  window.dispatchEvent(new Event(AUTH_SESSION_EVENT))
}

export function getAuthHeaders(headers?: HeadersInit): Headers {
  const merged = new Headers(headers)
  const token = getAccessToken()
  if (token) {
    merged.set('Authorization', `${getTokenType()} ${token}`)
  }
  return merged
}

export async function authFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  return fetch(input, {
    ...init,
    headers: getAuthHeaders(init?.headers),
  })
}

export async function getApiErrorMessage(res: Response): Promise<string> {
  const isAuthEndpoint = /\/auth\/(login|signup)\/?$/i.test(res.url)
  if (res.status === 401 && !isAuthEndpoint) return 'Authentication required. Sign in and retry.'
  if (res.status === 403) return 'Your account does not have access to this feature.'

  try {
    const body = await res.clone().json()
    return body.detail ?? body.error ?? res.statusText ?? 'Request failed'
  } catch {
    try {
      const text = await res.text()
      return text || res.statusText || 'Request failed'
    } catch {
      return res.statusText || 'Request failed'
    }
  }
}