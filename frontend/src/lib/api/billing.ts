import { authFetch, getApiErrorMessage } from '@/lib/auth'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'

export type BillingStatus = {
  tier: string
  subscription_status: string
  stripe_customer_id: string | null
  stripe_subscription_id: string | null
}

/** Create a Stripe Checkout session and return the redirect URL. */
export async function subscribePro(): Promise<{ success: boolean; checkout_url?: string; error?: string }> {
  try {
    const res = await authFetch(`${API_BASE}/billing/subscribe`, { method: 'POST' })
    if (!res.ok) return { success: false, error: await getApiErrorMessage(res) }
    const data = await res.json()
    return { success: true, checkout_url: data.checkout_url }
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' }
  }
}

/** Create a Stripe Customer Portal session and return the redirect URL. */
export async function createPortalSession(): Promise<{ success: boolean; portal_url?: string; error?: string }> {
  try {
    const res = await authFetch(`${API_BASE}/billing/portal`, { method: 'POST' })
    if (!res.ok) return { success: false, error: await getApiErrorMessage(res) }
    const data = await res.json()
    return { success: true, portal_url: data.portal_url }
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' }
  }
}

/** Fetch the current user's billing status. */
export async function getBillingStatus(): Promise<{ success: boolean; data?: BillingStatus; error?: string }> {
  try {
    const res = await authFetch(`${API_BASE}/billing/status`)
    if (!res.ok) return { success: false, error: await getApiErrorMessage(res) }
    const data: BillingStatus = await res.json()
    return { success: true, data }
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : 'Network error' }
  }
}
