'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { Check } from 'lucide-react'
import { getBillingStatus, subscribePro, createPortalSession, type BillingStatus } from '@/lib/api/billing'

// ─────────────────────────────────────────────────────────────────────────────
// Feature lists
// ─────────────────────────────────────────────────────────────────────────────

const FREE_FEATURES = [
  '5 projection runs per day',
  'NBA slate uploads (CSV)',
  'Player projection table',
  'Basic ownership estimates',
  'Floor / ceiling / value columns',
]

const PRO_FEATURES = [
  'Unlimited projection runs',
  'Full lineup optimizer (FD + DK)',
  'Advanced exposure controls',
  'Game-stack + correlation settings',
  'Player pool lock / fade / override',
  'EV modeling & contest simulation',
  'Late-swap candidate pool',
  'Analytics dashboard (ROI, accuracy)',
  'Priority support',
]

// ─────────────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────────────

function Feature({ text }: { text: string }) {
  return (
    <li className="flex items-start gap-2 text-sm text-text-secondary">
      <Check className="w-4 h-4 text-success shrink-0 mt-0.5" />
      {text}
    </li>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Page
// ─────────────────────────────────────────────────────────────────────────────

export default function BillingPage() {
  const router = useRouter()
  const [billing, setBilling] = useState<BillingStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [upgrading, setUpgrading] = useState(false)
  const [managing, setManaging] = useState(false)

  useEffect(() => {
    getBillingStatus().then(res => {
      if (res.success && res.data) setBilling(res.data)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  const tier = billing?.tier ?? 'free'
  const isPro = tier === 'pro' || tier === 'elite' || tier === 'admin'
  const hasCustomer = !!billing?.stripe_customer_id

  async function handleUpgrade() {
    setUpgrading(true)
    const res = await subscribePro()
    setUpgrading(false)
    if (res.success && res.checkout_url) {
      window.location.href = res.checkout_url
    }
  }

  async function handleManage() {
    setManaging(true)
    const res = await createPortalSession()
    setManaging(false)
    if (res.success && res.portal_url) {
      window.location.href = res.portal_url
    }
  }

  return (
    <div className="bg-surface-base min-h-[calc(100vh-48px)] p-6">
      <div className="max-w-3xl mx-auto">

        {/* Header */}
        <div className="text-center mb-10">
          <h1 className="text-3xl font-extrabold text-text-primary m-0">Plans &amp; Pricing</h1>
          <p className="text-text-muted text-sm mt-2 m-0">
            Start free. Upgrade when you&apos;re ready to win more.
          </p>
        </div>

        {/* Already subscribed banner */}
        {!loading && isPro && (
          <div className="mb-6 px-4 py-3 bg-success-muted border border-success/40 rounded-lg text-sm text-success text-center font-medium">
            You&apos;re on the <strong className="capitalize">{tier}</strong> plan — all features are unlocked.
            {hasCustomer && (
              <button
                onClick={handleManage}
                disabled={managing}
                className="ml-3 underline text-success hover:no-underline transition-all cursor-pointer bg-transparent border-none text-sm font-medium"
              >
                {managing ? 'Opening…' : 'Manage subscription'}
              </button>
            )}
          </div>
        )}

        {/* Tier cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">

          {/* Free */}
          <div className={`bg-surface-raised border rounded-xl p-6 flex flex-col ${!isPro ? 'border-primary ring-1 ring-primary/30' : 'border-surface-border'}`}>
            <div className="mb-1">
              <span className="text-xs font-bold uppercase text-text-muted tracking-wider">Free</span>
              {!isPro && !loading && (
                <span className="ml-2 px-2 py-0.5 rounded-full bg-primary/15 text-primary text-[10px] font-bold uppercase">Current</span>
              )}
            </div>
            <div className="mb-4">
              <span className="text-4xl font-extrabold text-text-primary">$0</span>
              <span className="text-text-muted text-sm ml-1">/ month</span>
            </div>
            <ul className="flex flex-col gap-2.5 mb-6 list-none p-0 m-0">
              {FREE_FEATURES.map(f => <Feature key={f} text={f} />)}
            </ul>
            <div className="mt-auto">
              {isPro ? (
                <button
                  onClick={() => router.push('/settings')}
                  className="w-full py-2 rounded text-sm font-semibold bg-surface-overlay border border-surface-border text-text-secondary hover:border-surface-border/50 cursor-pointer transition-colors"
                >
                  Downgrade
                </button>
              ) : (
                <span className="block w-full py-2 rounded text-sm font-semibold bg-surface-border text-text-muted text-center cursor-default select-none">
                  Current plan
                </span>
              )}
            </div>
          </div>

          {/* Pro */}
          <div className={`bg-surface-raised border rounded-xl p-6 flex flex-col relative overflow-hidden ${isPro ? 'border-primary ring-1 ring-primary/30' : 'border-primary/60'}`}>
            {/* Popular ribbon */}
            <div className="absolute top-3 right-3 px-2.5 py-0.5 rounded-full bg-primary text-white text-[10px] font-bold uppercase">
              {isPro ? 'Active' : 'Most Popular'}
            </div>
            <div className="mb-1">
              <span className="text-xs font-bold uppercase text-primary tracking-wider">Pro</span>
              {isPro && !loading && (
                <span className="ml-2 px-2 py-0.5 rounded-full bg-primary/15 text-primary text-[10px] font-bold uppercase">Current</span>
              )}
            </div>
            <div className="mb-4">
              <span className="text-4xl font-extrabold text-text-primary">$29</span>
              <span className="text-text-muted text-sm ml-1">/ month</span>
            </div>
            <ul className="flex flex-col gap-2.5 mb-6 list-none p-0 m-0">
              {PRO_FEATURES.map(f => <Feature key={f} text={f} />)}
            </ul>
            <div className="mt-auto">
              {loading ? (
                <div className="w-full py-2 rounded bg-surface-border animate-pulse h-9" />
              ) : isPro ? (
                hasCustomer ? (
                  <button
                    onClick={handleManage}
                    disabled={managing}
                    className="w-full py-2.5 rounded text-sm font-bold bg-primary/10 text-primary border border-primary/40 hover:bg-primary/20 disabled:opacity-50 cursor-pointer transition-colors"
                  >
                    {managing ? 'Opening portal…' : 'Manage subscription'}
                  </button>
                ) : (
                  <span className="block w-full py-2 rounded text-sm font-semibold bg-primary/10 text-primary text-center cursor-default select-none">
                    Active
                  </span>
                )
              ) : (
                <button
                  onClick={handleUpgrade}
                  disabled={upgrading}
                  className="w-full py-2.5 rounded text-sm font-bold bg-primary text-white hover:bg-primary/90 disabled:opacity-50 cursor-pointer transition-colors"
                >
                  {upgrading ? 'Redirecting to checkout…' : 'Upgrade to Pro — $29/mo'}
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Fine print */}
        <p className="text-center text-xs text-text-muted mt-6">
          Cancel anytime from your{' '}
          <Link href="/settings" className="underline hover:text-text-secondary transition-colors">
            Settings page
          </Link>
          . Payments processed securely by Stripe.
        </p>

      </div>
    </div>
  )
}
