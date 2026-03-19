'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { Shield, CreditCard, Trash2, Key, ExternalLink } from 'lucide-react'
import { authFetch, clearAuthSession, getStoredUser } from '@/lib/auth'
import { getBillingStatus, subscribePro, createPortalSession, type BillingStatus } from '@/lib/api/billing'
import { useToastStore } from '@/store/toastStore'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'

// ── Tier badge ────────────────────────────────────────────────────────────
function TierBadge({ tier }: { tier: string }) {
  const styles: Record<string, string> = {
    pro:   'bg-primary/15 text-primary border border-primary/40',
    elite: 'bg-[#f59e0b]/15 text-[#f59e0b] border border-[#f59e0b]/40',
    free:  'bg-surface-overlay text-text-muted border border-surface-border',
  }
  return (
    <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold uppercase tracking-wide ${styles[tier] ?? styles.free}`}>
      {tier}
    </span>
  )
}

// ── Section card ─────────────────────────────────────────────────────────
function Card({ title, icon: Icon, children }: { title: string; icon: React.ElementType; children: React.ReactNode }) {
  return (
    <div className="bg-surface-raised border border-surface-border rounded-xl overflow-hidden">
      <div className="px-5 py-3.5 border-b border-surface-border flex items-center gap-2">
        <Icon className="w-4 h-4 text-text-muted" />
        <span className="text-sm font-bold text-text-primary">{title}</span>
      </div>
      <div className="px-5 py-4">{children}</div>
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────
export default function SettingsPage() {
  const router = useRouter()
  const { toast } = useToastStore()
  const storedUser = getStoredUser()

  const [billing, setBilling] = useState<BillingStatus | null>(null)
  const [billingLoading, setBillingLoading] = useState(true)
  const [portalLoading, setPortalLoading] = useState(false)
  const [subscribeLoading, setSubscribeLoading] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)

  useEffect(() => {
    getBillingStatus().then(res => {
      if (res.success && res.data) setBilling(res.data)
      setBillingLoading(false)
    })
  }, [])

  async function handleManageBilling() {
    setPortalLoading(true)
    const res = await createPortalSession()
    setPortalLoading(false)
    if (res.success && res.portal_url) {
      window.location.href = res.portal_url
    } else {
      toast(res.error ?? 'Could not open billing portal', 'error')
    }
  }

  async function handleSubscribe() {
    setSubscribeLoading(true)
    const res = await subscribePro()
    setSubscribeLoading(false)
    if (res.success && res.checkout_url) {
      window.location.href = res.checkout_url
    } else {
      toast(res.error ?? 'Could not start checkout', 'error')
    }
  }

  async function handleDeleteAccount() {
    setDeleting(true)
    try {
      const res = await authFetch(`${API_BASE}/auth/me`, { method: 'DELETE' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        toast((data.detail ?? data.error ?? 'Delete failed') as string, 'error')
        return
      }
      clearAuthSession()
      router.push('/auth')
    } catch {
      toast('Network error — please try again', 'error')
    } finally {
      setDeleting(false)
      setConfirmOpen(false)
    }
  }

  const tier = billing?.tier ?? storedUser?.tier ?? 'free'
  const isPro = tier === 'pro' || tier === 'elite'
  const hasCustomer = !!billing?.stripe_customer_id

  return (
    <div className="bg-surface-base min-h-[calc(100vh-48px)] p-6">
      <div className="max-w-2xl mx-auto flex flex-col gap-5">
        <div>
          <h1 className="text-[22px] font-extrabold text-text-primary m-0">Settings</h1>
          <p className="text-sm text-text-muted mt-1 m-0">Manage your account, plan, and data.</p>
        </div>

        {/* Account */}
        <Card title="Account" icon={Shield}>
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
            <dt className="text-text-muted">Email</dt>
            <dd className="text-text-primary font-medium">{storedUser?.email ?? '—'}</dd>
            <dt className="text-text-muted">Name</dt>
            <dd className="text-text-primary">{storedUser?.full_name || <span className="text-text-muted italic">Not set</span>}</dd>
            <dt className="text-text-muted">Plan</dt>
            <dd><TierBadge tier={tier} /></dd>
          </dl>
          <div className="mt-4">
            <button
              onClick={() => router.push('/auth?mode=forgot')}
              className="flex items-center gap-1.5 text-sm text-text-secondary hover:text-text-primary transition-colors"
            >
              <Key className="w-3.5 h-3.5" />
              Change password
            </button>
          </div>
        </Card>

        {/* Billing */}
        <Card title="Billing & Subscription" icon={CreditCard}>
          {billingLoading ? (
            <p className="text-sm text-text-muted">Loading billing info…</p>
          ) : isPro ? (
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-2">
                <span className="text-sm text-text-secondary">Current plan:</span>
                <TierBadge tier={tier} />
                <span className="text-xs text-text-muted ml-1">
                  ({billing?.subscription_status ?? 'active'})
                </span>
              </div>
              {hasCustomer && (
                <button
                  onClick={handleManageBilling}
                  disabled={portalLoading}
                  className="flex items-center gap-1.5 w-fit px-4 py-2 rounded text-sm font-semibold bg-surface-overlay border border-surface-border text-text-primary hover:border-surface-border/70 disabled:opacity-50 cursor-pointer transition-colors"
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                  {portalLoading ? 'Opening portal…' : 'Manage subscription'}
                </button>
              )}
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              <p className="text-sm text-text-secondary m-0">
                Upgrade to <strong className="text-text-primary">Pro</strong> for unlimited projections, lineup optimization, and EV modeling.
              </p>
              <button
                onClick={handleSubscribe}
                disabled={subscribeLoading}
                className="w-fit px-5 py-2 rounded text-sm font-bold bg-primary text-white hover:bg-primary/90 disabled:opacity-50 cursor-pointer transition-colors"
              >
                {subscribeLoading ? 'Redirecting…' : 'Upgrade to Pro — $29/mo'}
              </button>
            </div>
          )}
        </Card>

        {/* Danger zone */}
        <Card title="Danger Zone" icon={Trash2}>
          <p className="text-sm text-text-secondary mb-4 mt-0">
            Permanently delete your account and all associated data. This action cannot be undone.
          </p>
          <button
            onClick={() => setConfirmOpen(true)}
            className="flex items-center gap-1.5 px-4 py-2 rounded text-sm font-semibold bg-danger-muted text-danger border border-danger/40 hover:border-danger/70 cursor-pointer transition-colors"
          >
            <Trash2 className="w-3.5 h-3.5" />
            Delete my account
          </button>
        </Card>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        title="Delete account"
        message="This permanently erases your account, lineups, and all associated data. There is no undo."
        confirmLabel="Yes, delete my account"
        variant="danger"
        isLoading={deleting}
        onConfirm={handleDeleteAccount}
        onCancel={() => setConfirmOpen(false)}
      />
    </div>
  )
}
