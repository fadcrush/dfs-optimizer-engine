'use client'

import { Suspense, useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { CheckCircle } from 'lucide-react'
import { getBillingStatus } from '@/lib/api/billing'

function BillingSuccessContent() {
  const router = useRouter()
  const params = useSearchParams()
  const sessionId = params.get('session_id')
  const [tier, setTier] = useState<string | null>(null)

  // Fetch billing status to confirm upgrade (Stripe webhook may lag a few seconds)
  useEffect(() => {
    let attempts = 0
    const poll = async () => {
      const res = await getBillingStatus()
      if (res.success && res.data?.tier && res.data.tier !== 'free') {
        setTier(res.data.tier)
        return
      }
      attempts++
      if (attempts < 6) setTimeout(poll, 2000)
      else setTier('pro') // assume success after 12s
    }
    poll()
  }, [sessionId])

  return (
    <div className="min-h-screen bg-surface-base flex items-center justify-center p-6">
      <div className="max-w-md w-full bg-surface-raised border border-surface-border rounded-2xl p-8 text-center">
        <CheckCircle className="w-12 h-12 text-success mx-auto mb-4" />
        <h1 className="text-2xl font-extrabold text-text-primary mb-2">
          {tier ? `Welcome to ${tier.charAt(0).toUpperCase() + tier.slice(1)}!` : 'Processing…'}
        </h1>
        <p className="text-sm text-text-secondary mb-6">
          {tier
            ? 'Your subscription is active. All Pro features are now unlocked.'
            : 'Confirming your subscription — this only takes a moment.'}
        </p>
        {tier && (
          <div className="flex flex-col gap-3">
            <button
              onClick={() => router.push('/optimizer')}
              className="w-full py-2.5 rounded text-sm font-bold bg-primary text-white hover:bg-primary/90 cursor-pointer transition-colors"
            >
              Start optimizing lineups
            </button>
            <Link href="/settings" className="text-sm text-text-muted hover:text-text-secondary transition-colors">
              View account settings
            </Link>
          </div>
        )}
      </div>
    </div>
  )
}

export default function BillingSuccessPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-surface-base flex items-center justify-center">
        <div className="text-text-secondary text-sm">Loading…</div>
      </div>
    }>
      <BillingSuccessContent />
    </Suspense>
  )
}
