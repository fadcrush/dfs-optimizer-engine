'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { resetPassword } from '@/lib/api/auth'

const cardCls =
  'bg-surface-raised border border-surface-border rounded-2xl shadow-[0_18px_60px_rgba(0,0,0,0.28)]'
const inputCls =
  'w-full rounded-xl border border-surface-border bg-surface-overlay px-3.5 py-3 text-sm text-text-primary outline-none focus:border-primary'

export default function ResetPasswordPage() {
  const router = useRouter()
  const [token, setToken] = useState<string | null>(null)
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  useEffect(() => {
    if (typeof window === 'undefined') return
    const t = new URLSearchParams(window.location.search).get('token')
    setToken(t)
  }, [])

  const handleSubmit = async () => {
    if (!token) {
      setError('Missing reset token. Please use the link from your email.')
      return
    }
    if (password.length < 8) {
      setError('Password must be at least 8 characters.')
      return
    }
    if (password !== confirm) {
      setError('Passwords do not match.')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await resetPassword(token, password)
      if (!res.success) {
        setError(res.error ?? 'Reset failed. The link may have expired.')
        return
      }
      setSuccess(true)
      setTimeout(() => router.push('/auth'), 3000)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(30,64,175,0.18),_transparent_36%),linear-gradient(180deg,_#06101c_0%,_#0b1320_100%)] flex items-center justify-center px-6 py-10">
      <div className={`${cardCls} w-full max-w-md p-6`}>
        <h1 className="text-xl font-extrabold text-text-primary mb-1">Set new password</h1>
        <p className="text-sm text-text-muted mb-5">Enter and confirm your new password below.</p>

        {success ? (
          <div className="rounded-xl border border-success/30 bg-success/10 px-4 py-4 text-sm text-success space-y-2">
            <p className="font-semibold">Password updated successfully!</p>
            <p>Redirecting you to sign in…</p>
          </div>
        ) : !token ? (
          <div className="rounded-xl border border-danger/30 bg-danger/10 px-4 py-4 text-sm text-danger space-y-2">
            <p className="font-semibold">Invalid reset link</p>
            <p>This link is missing a token. Please request a new password reset.</p>
            <Link href="/auth" className="underline hover:opacity-80">Go to sign in →</Link>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <div>
              <label className="mb-1.5 block text-[11px] font-semibold uppercase text-text-muted">
                New Password
              </label>
              <input
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={inputCls}
                type="password"
                placeholder="At least 8 characters"
              />
            </div>
            <div>
              <label className="mb-1.5 block text-[11px] font-semibold uppercase text-text-muted">
                Confirm Password
              </label>
              <input
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className={inputCls}
                type="password"
                placeholder="Repeat password"
                onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
              />
            </div>

            {error && (
              <div className="rounded-xl border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
                {error}
              </div>
            )}

            <button
              onClick={handleSubmit}
              disabled={loading || !password || !confirm}
              className="mt-1 w-full rounded-xl bg-primary px-4 py-3 text-sm font-bold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? 'Updating…' : 'Set New Password'}
            </button>

            <p className="text-center text-[11px] text-text-muted">
              <Link href="/auth" className="underline hover:text-text-secondary">
                Back to sign in
              </Link>
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
