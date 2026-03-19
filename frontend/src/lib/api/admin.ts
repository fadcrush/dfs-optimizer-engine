import { authFetch, getApiErrorMessage } from '@/lib/auth'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'

export type AdminStats = {
  total_users: number
  pro_users: number
  free_users: number
  new_users_30d: number
  mrr: number
}

export type AdminUser = {
  id: string
  email: string
  full_name: string | null
  tier: string
  subscription_status: string
  email_verified: boolean
  created_at: string | null
  last_login_at: string | null
  lineups_generated: number
  slates_processed: number
  is_admin: boolean
}

export type AdminUsersResponse = {
  users: AdminUser[]
  total: number
  page: number
  per_page: number
  pages: number
}

export async function getAdminStats(): Promise<AdminStats> {
  const res = await authFetch(`${API_BASE}/admin/stats`)
  if (!res.ok) throw new Error(await getApiErrorMessage(res))
  return res.json()
}

export async function getAdminUsers(params?: {
  page?: number
  per_page?: number
  tier?: string
  search?: string
}): Promise<AdminUsersResponse> {
  const qs = new URLSearchParams()
  if (params?.page) qs.set('page', String(params.page))
  if (params?.per_page) qs.set('per_page', String(params.per_page))
  if (params?.tier) qs.set('tier', params.tier)
  if (params?.search) qs.set('search', params.search)
  const url = `${API_BASE}/admin/users${qs.toString() ? `?${qs}` : ''}`
  const res = await authFetch(url)
  if (!res.ok) throw new Error(await getApiErrorMessage(res))
  return res.json()
}
