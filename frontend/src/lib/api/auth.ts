import { authFetch, getApiErrorMessage, storeAuthSession, storeUserData } from '@/lib/auth'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'

export type AuthUser = {
  id: string
  email: string
  full_name?: string | null
  tier?: string
  subscription_status?: string
  email_verified?: boolean
  is_admin?: boolean
}

type AuthResponse = {
  access_token: string
  token_type: string
  user: AuthUser
}

export async function login(email: string, password: string): Promise<{ success: boolean; data?: AuthResponse; error?: string }> {
  const res = await fetch(API_BASE + '/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
  if (!res.ok) return { success: false, error: await getApiErrorMessage(res) }
  const data = (await res.json()) as AuthResponse
  storeAuthSession(data.access_token, data.token_type)
  if (data.user) storeUserData(data.user)
  return { success: true, data }
}

export async function signup(fullName: string, email: string, password: string): Promise<{ success: boolean; data?: AuthResponse; error?: string }> {
  const res = await fetch(API_BASE + '/auth/signup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ full_name: fullName, email, password }),
  })
  if (!res.ok) return { success: false, error: await getApiErrorMessage(res) }
  const data = (await res.json()) as AuthResponse
  storeAuthSession(data.access_token, data.token_type)
  if (data.user) storeUserData(data.user)
  return { success: true, data }
}

export async function getCurrentUser(): Promise<{ success: boolean; data?: AuthUser; error?: string }> {
  const res = await authFetch(API_BASE + '/auth/me')
  if (!res.ok) return { success: false, error: await getApiErrorMessage(res) }
  const data = (await res.json()) as AuthUser
  storeUserData(data)
  return { success: true, data }
}

export async function requestPasswordReset(email: string): Promise<{ success: boolean; error?: string }> {
  const res = await fetch(API_BASE + '/auth/request-password-reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  })
  if (!res.ok) return { success: false, error: await getApiErrorMessage(res) }
  return { success: true }
}

export async function resetPassword(token: string, newPassword: string): Promise<{ success: boolean; error?: string }> {
  const res = await fetch(API_BASE + '/auth/reset-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, new_password: newPassword }),
  })
  if (!res.ok) return { success: false, error: await getApiErrorMessage(res) }
  return { success: true }
}