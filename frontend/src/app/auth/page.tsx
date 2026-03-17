'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { AUTH_DISABLED, clearAuthSession } from '@/lib/auth'
import { getCurrentUser, login, signup } from '@/lib/api/auth'

type Mode = 'login' | 'signup'

const cardCls = 'bg-surface-raised border border-surface-border rounded-2xl shadow-[0_18px_60px_rgba(0,0,0,0.28)]'
const inputCls = 'w-full rounded-xl border border-surface-border bg-surface-overlay px-3.5 py-3 text-sm text-text-primary outline-none focus:border-primary'

export default function AuthPage() {
  const router = useRouter()
  const [mode, setMode] = useState<Mode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [nextPath, setNextPath] = useState('/')

  useEffect(() => {
    if (typeof window === 'undefined') return
    const requested = new URLSearchParams(window.location.search).get('next')
    if (!requested || !requested.startsWith('/')) {
      setNextPath('/')
      return
    }
    if (requested.startsWith('//')) {
      setNextPath('/')
      return
    }
    setNextPath(requested)
  }, [])

  useEffect(() => {
    if (!AUTH_DISABLED) return
    router.replace(nextPath)
  }, [nextPath, router])

  const handleSubmit = async () => {
    setLoading(true)
    setError(null)
    setMessage(null)
    try {
      const res = mode === 'login'
        ? await login(email, password)
        : await signup(fullName, email, password)

      if (!res.success) {
        setError(res.error ?? 'Authentication failed')
        return
      }

      const me = await getCurrentUser()
      const userName = me.data?.full_name || me.data?.email || email
      setMessage(`Signed in as ${userName}`)
      router.push(nextPath)
      router.refresh()
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-[calc(100vh-48px)] bg-[radial-gradient(circle_at_top,_rgba(30,64,175,0.18),_transparent_36%),linear-gradient(180deg,_#06101c_0%,_#0b1320_100%)] px-6 py-10">
      <div className="mx-auto max-w-[980px] grid gap-6 lg:grid-cols-[1.1fr_0.9fr] items-start">
        <section className="pt-6">
          <div className="inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/10 px-3 py-1 text-[11px] font-semibold text-[#93c5fd]">
            Auth Required
          </div>
          <h1 className="mt-4 text-[34px] font-extrabold tracking-tight text-text-primary">Secure your DFS workspace</h1>
          <p className="mt-3 max-w-[560px] text-sm leading-6 text-text-muted">
            Slate uploads, generated projections, optimizer exports, and late-swap downloads now use authenticated per-user storage. Sign in once and the app will attach your bearer token automatically.
          </p>
          <div className="mt-6 grid gap-3 sm:grid-cols-2">
            {[
              'Per-user slate storage and downloads',
              'No shared export filenames across accounts',
              'Protected optimizer, projections, and pipeline routes',
              'Local token storage for browser requests',
            ].map(item => (
              <div key={item} className="rounded-2xl border border-surface-border bg-surface-overlay/70 px-4 py-3 text-sm text-text-secondary">
                {item}
              </div>
            ))}
          </div>
        </section>

        <section className={`${cardCls} p-5`}>
          {AUTH_DISABLED ? (
            <div className="space-y-4">
              <div className="rounded-xl border border-success/30 bg-success/10 px-4 py-3 text-sm text-success">
                Sign-in is temporarily paused in this environment. Continue directly to the app.
              </div>
              <button
                onClick={() => router.replace(nextPath)}
                className="w-full rounded-xl bg-primary px-4 py-3 text-sm font-bold text-white transition-colors hover:bg-primary-hover"
              >
                Continue to App
              </button>
            </div>
          ) : (
            <>
          <div className="flex gap-1 rounded-xl bg-surface-overlay p-1">
            {(['login', 'signup'] as Mode[]).map(value => (
              <button
                key={value}
                onClick={() => { setMode(value); setError(null); setMessage(null) }}
                className={`flex-1 rounded-lg px-3 py-2 text-sm font-bold transition-colors ${mode === value ? 'bg-primary text-white' : 'text-text-muted hover:text-text-primary'}`}
              >
                {value === 'login' ? 'Sign In' : 'Create Account'}
              </button>
            ))}
          </div>

          <div className="mt-5 flex flex-col gap-3">
            {mode === 'signup' && (
              <div>
                <label className="mb-1.5 block text-[11px] font-semibold uppercase text-text-muted">Full Name</label>
                <input value={fullName} onChange={e => setFullName(e.target.value)} className={inputCls} placeholder="DFS Edge user" />
              </div>
            )}
            <div>
              <label className="mb-1.5 block text-[11px] font-semibold uppercase text-text-muted">Email</label>
              <input value={email} onChange={e => setEmail(e.target.value)} className={inputCls} type="email" placeholder="you@example.com" />
            </div>
            <div>
              <label className="mb-1.5 block text-[11px] font-semibold uppercase text-text-muted">Password</label>
              <input value={password} onChange={e => setPassword(e.target.value)} className={inputCls} type="password" placeholder="At least 8 characters" />
            </div>
          </div>

          {error && <div className="mt-4 rounded-xl border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">{error}</div>}
          {message && <div className="mt-4 rounded-xl border border-success/30 bg-success/10 px-3 py-2 text-sm text-success">{message}</div>}

          <div className="mt-5 flex gap-2">
            <button
              onClick={handleSubmit}
              disabled={loading || !email || !password || (mode === 'signup' && !fullName.trim())}
              className="flex-1 rounded-xl bg-primary px-4 py-3 text-sm font-bold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? 'Working…' : mode === 'login' ? 'Sign In' : 'Create Account'}
            </button>
            <button
              onClick={() => { clearAuthSession(); setMessage('Signed out locally'); setError(null) }}
              className="rounded-xl border border-surface-border bg-surface-overlay px-4 py-3 text-sm font-semibold text-text-secondary transition-colors hover:bg-surface-border"
            >
              Clear Token
            </button>
          </div>
            </>
          )}
        </section>
      </div>
    </div>
  )
}