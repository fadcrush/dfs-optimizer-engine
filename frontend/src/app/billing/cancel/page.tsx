'use client'

import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { XCircle } from 'lucide-react'

export default function BillingCancelPage() {
  const router = useRouter()

  return (
    <div className="min-h-screen bg-surface-base flex items-center justify-center p-6">
      <div className="max-w-md w-full bg-surface-raised border border-surface-border rounded-2xl p-8 text-center">
        <XCircle className="w-12 h-12 text-text-muted mx-auto mb-4" />
        <h1 className="text-2xl font-extrabold text-text-primary mb-2">Checkout cancelled</h1>
        <p className="text-sm text-text-secondary mb-6">
          No charge was made. You can upgrade any time from your Settings page.
        </p>
        <div className="flex flex-col gap-3">
          <button
            onClick={() => router.push('/settings')}
            className="w-full py-2.5 rounded text-sm font-bold bg-primary text-white hover:bg-primary/90 cursor-pointer transition-colors"
          >
            Back to Settings
          </button>
          <Link href="/" className="text-sm text-text-muted hover:text-text-secondary transition-colors">
            Return to dashboard
          </Link>
        </div>
      </div>
    </div>
  )
}
