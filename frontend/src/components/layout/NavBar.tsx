'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import {
  AUTH_DISABLED,
  AUTH_SESSION_EVENT,
  clearAuthSession,
  getStoredUser,
  type StoredUser,
} from '@/lib/auth'

const NAV_ITEMS = [
  { href: '/',             label: 'Home'        },
  { href: '/slates',       label: 'Slates'      },
  { href: '/projections',  label: 'Projections' },
  { href: '/optimizer',    label: 'Optimizer'   },
  { href: '/ev-modeling',  label: 'EV Modeling' },
  { href: '/simulation',   label: 'Simulation'  },
  { href: '/late-swap',    label: 'Late Swap'   },
  { href: '/analytics',    label: 'Analytics'   },
  { href: '/metrics',      label: 'Metrics'     },
]

function useClock() {
  const [display, setDisplay] = useState('')
  useEffect(() => {
    const fmt = () => {
      const now = new Date()
      const day = now.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
      const time = now.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
      setDisplay(`${day}  ${time}`)
    }
    fmt()
    const id = setInterval(fmt, 30_000)
    return () => clearInterval(id)
  }, [])
  return display
}

function useCurrentUser() {
  const [user, setUser] = useState<StoredUser | null>(null)
  useEffect(() => {
    setUser(getStoredUser())
    const handler = () => setUser(getStoredUser())
    window.addEventListener(AUTH_SESSION_EVENT, handler)
    return () => window.removeEventListener(AUTH_SESSION_EVENT, handler)
  }, [])
  return user
}

export function NavBar() {
  const path = usePathname()
  const router = useRouter()
  const clock = useClock()
  const user = useCurrentUser()
  const [menuOpen, setMenuOpen] = useState(false)

  // Close mobile menu on route change
  useEffect(() => { setMenuOpen(false) }, [path])

  const initials = (() => {
    if (!user) return null
    const name = user.full_name?.trim()
    if (name) {
      const parts = name.split(' ')
      return parts.length >= 2
        ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
        : parts[0][0].toUpperCase()
    }
    return user.email?.[0]?.toUpperCase() ?? '?'
  })()

  const handleSignOut = () => {
    clearAuthSession()
    router.push('/auth')
  }

  return (
    <>
    <nav className="bg-[#1e2d40] border-b border-[#334155] px-5 flex items-center h-12 sticky top-0 z-[100]">
      {/* Brand */}
      <span className="font-extrabold text-sm text-[#3b82f6] tracking-[-0.3px] mr-5 whitespace-nowrap">
        DFS Edge Pro
      </span>

      {/* Nav links — desktop only */}
      <div className="hidden md:flex gap-0.5 flex-1 overflow-x-auto">
        {NAV_ITEMS.map(({ href, label }) => {
          const active = path === href || (href !== '/' && path.startsWith(href))
          return (
            <Link
              key={href}
              href={href}
              className={`px-3 py-[5px] rounded-[5px] text-xs no-underline whitespace-nowrap transition-[background,color] duration-150 ${active ? 'font-semibold text-white bg-[#2563eb]' : 'font-medium text-[#cbd5e1] bg-transparent'}`}
            >
              {label}
            </Link>
          )
        })}
      </div>

      {/* Right: clock + user + hamburger */}
      <div className="flex items-center gap-3 ml-auto">
        {clock && (
          <span className="text-[11px] text-text-muted whitespace-nowrap">
            {clock}
          </span>
        )}

        {!AUTH_DISABLED && !user ? (
          <Link
            href="/auth"
            className="text-[11px] font-semibold text-[#3b82f6] hover:text-white transition-colors whitespace-nowrap no-underline"
          >
            Sign in
          </Link>
        ) : (
          <div className="flex items-center gap-2">
            {user && (
              <span className="text-[11px] text-text-muted whitespace-nowrap hidden sm:block max-w-[120px] truncate" title={user.email}>
                {user.full_name?.trim() || user.email}
              </span>
            )}
            <button
              onClick={!AUTH_DISABLED ? handleSignOut : undefined}
              title={user ? `Signed in as ${user.email}` : 'Local dev'}
              className="w-7 h-7 rounded-full bg-[#3b82f6] flex items-center justify-center text-[11px] font-bold text-white shrink-0 border-0 cursor-pointer hover:bg-[#2563eb] transition-colors"
            >
              {initials ?? (AUTH_DISABLED ? 'D' : '?')}
            </button>
          </div>
        )}

        {/* Hamburger — mobile only */}
        <button
          onClick={() => setMenuOpen(m => !m)}
          aria-label="Toggle navigation menu"
          aria-expanded={menuOpen}
          className="md:hidden flex flex-col justify-center items-center w-7 h-7 gap-[4px] cursor-pointer bg-transparent border-0 p-0 shrink-0"
        >
          <span className={`block w-5 h-0.5 bg-[#cbd5e1] origin-center transition-transform duration-200 ${menuOpen ? 'rotate-45 translate-y-[6px]' : ''}`} />
          <span className={`block w-5 h-0.5 bg-[#cbd5e1] transition-opacity duration-200 ${menuOpen ? 'opacity-0' : ''}`} />
          <span className={`block w-5 h-0.5 bg-[#cbd5e1] origin-center transition-transform duration-200 ${menuOpen ? '-rotate-45 -translate-y-[6px]' : ''}`} />
        </button>
      </div>
    </nav>

    {/* Mobile drawer */}
    {menuOpen && (
      <div className="md:hidden bg-[#1e2d40] border-b border-[#334155] sticky top-12 z-[99] px-3 py-2 flex flex-col gap-0.5">
        {NAV_ITEMS.map(({ href, label }) => {
          const active = path === href || (href !== '/' && path.startsWith(href))
          return (
            <Link
              key={href}
              href={href}
              onClick={() => setMenuOpen(false)}
              className={`px-3 py-2 rounded-[5px] text-sm no-underline transition-[background,color] duration-150 ${active ? 'font-semibold text-white bg-[#2563eb]' : 'font-medium text-[#cbd5e1]'}`}
            >
              {label}
            </Link>
          )
        })}
      </div>
    )}
  </>
  )
}
