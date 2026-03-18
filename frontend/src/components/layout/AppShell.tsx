'use client'
import { useEffect, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import Link from 'next/link'
import { Sidebar } from './Sidebar'
import { cn } from '@/lib/utils'
import { AUTH_DISABLED, AUTH_SESSION_EVENT, getAccessToken } from '@/lib/auth'

const PUBLIC_PATHS = new Set(['/auth', '/auth/reset-password', '/privacy', '/terms'])

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const [collapsed, setCollapsed] = useState(false)
  const [hasSession, setHasSession] = useState<boolean | null>(null)
  const [nextPath, setNextPath] = useState(pathname)

  const isPublicRoute = PUBLIC_PATHS.has(pathname)

  useEffect(() => {
    if (typeof window === 'undefined') return
    const query = window.location.search
    setNextPath(query ? `${pathname}${query}` : pathname)
  }, [pathname])

  useEffect(() => {
    if (AUTH_DISABLED) {
      setHasSession(true)
      return
    }

    if (isPublicRoute) {
      setHasSession(true)
      return
    }

    const syncSession = () => setHasSession(Boolean(getAccessToken()))

    syncSession()
    window.addEventListener(AUTH_SESSION_EVENT, syncSession)
    window.addEventListener('storage', syncSession)

    return () => {
      window.removeEventListener(AUTH_SESSION_EVENT, syncSession)
      window.removeEventListener('storage', syncSession)
    }
  }, [isPublicRoute])

  useEffect(() => {
    if (isPublicRoute || hasSession !== false) return
    router.replace(`/auth?next=${encodeURIComponent(nextPath)}`)
  }, [hasSession, isPublicRoute, nextPath, router])

  if (!AUTH_DISABLED && !isPublicRoute && hasSession === null) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-surface-base px-6">
        <div className="max-w-md rounded-2xl border border-surface-border bg-surface-raised px-6 py-5 text-center shadow-[0_18px_60px_rgba(0,0,0,0.28)]">
          <div className="text-sm font-semibold text-text-primary">Loading workspace</div>
          <p className="mt-2 text-sm leading-6 text-text-muted">
            Checking your local session before loading protected DFS pages.
          </p>
        </div>
      </div>
    )
  }

  if (!AUTH_DISABLED && !isPublicRoute && hasSession === false) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-surface-base px-6">
        <div className="max-w-md rounded-2xl border border-surface-border bg-surface-raised px-6 py-5 text-center shadow-[0_18px_60px_rgba(0,0,0,0.28)]">
          <div className="text-sm font-semibold text-text-primary">Authentication required</div>
          <p className="mt-2 text-sm leading-6 text-text-muted">
            Redirecting to sign in before loading your protected DFS workspace.
          </p>
          <button
            onClick={() => router.replace(`/auth?next=${encodeURIComponent(nextPath)}`)}
            className="mt-4 rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover"
          >
            Continue to sign in
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-screen overflow-hidden bg-surface-base">
      {!isPublicRoute && <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((c) => !c)} />}
      <div
        className={cn(
          'flex flex-col flex-1 overflow-hidden transition-[padding] duration-200',
          isPublicRoute ? 'pl-0' : collapsed ? 'pl-16' : 'pl-60',
        )}
      >
        <main className="flex-1 overflow-y-auto scrollbar-thin">{children}</main>
        <footer className="shrink-0 border-t border-surface-border/50 px-6 py-2 flex items-center justify-end gap-4">
          <Link href="/privacy" className="text-[11px] text-text-muted hover:text-text-secondary transition-colors">
            Privacy Policy
          </Link>
          <Link href="/terms" className="text-[11px] text-text-muted hover:text-text-secondary transition-colors">
            Terms of Service
          </Link>
          <span className="text-[11px] text-text-muted/40 select-none">
            © {new Date().getFullYear()} DFS Edge Pro
          </span>
        </footer>
      </div>
    </div>
  )
}
