'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'
import {
  LayoutDashboard, Database, BarChart2, Zap, TrendingUp,
  Shuffle, ArrowLeftRight, LineChart, Activity,
  ChevronLeft, Bell, Moon, Sun, Menu, Search, Shield,
} from 'lucide-react'
import { useTheme } from 'next-themes'
import { useClock } from '@/hooks/useClock'
import { cn } from '@/lib/utils'
import { GlobalSearch } from './GlobalSearch'

const NAV_ITEMS = [
  { href: '/',            label: 'Dashboard',   icon: LayoutDashboard },
  { href: '/slates',      label: 'Slates',      icon: Database },
  { href: '/projections', label: 'Projections', icon: BarChart2 },
  { href: '/optimizer',   label: 'Optimizer',   icon: Zap },
  { href: '/ev-modeling', label: 'EV Modeling', icon: TrendingUp },
  { href: '/simulation',  label: 'Simulation',  icon: Shuffle },
  { href: '/late-swap',   label: 'Late Swap',   icon: ArrowLeftRight },
  { href: '/analytics',   label: 'Analytics',   icon: LineChart },
  { href: '/metrics',     label: 'Metrics',     icon: Activity },
  { href: '/auth',        label: 'Auth',        icon: Shield },
]

export function Sidebar({
  collapsed,
  onToggle,
}: {
  collapsed: boolean
  onToggle: () => void
}) {
  const path = usePathname()
  const { resolvedTheme, setTheme } = useTheme()
  const clock = useClock()
  const [searchOpen, setSearchOpen] = useState(false)
  const [mounted, setMounted] = useState(false)

  // Avoid hydration mismatch for theme
  useEffect(() => setMounted(true), [])

  // Global Cmd+K / Ctrl+K shortcut
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setSearchOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  return (
    <>
      <aside
        className={cn(
          'fixed top-0 left-0 h-full z-50 flex flex-col bg-surface-overlay border-r border-surface-border',
          'transition-[width] duration-200 overflow-hidden',
          collapsed ? 'w-16' : 'w-60',
        )}
      >
        {/* Brand + collapse toggle */}
        <div className="flex items-center justify-between h-14 px-3 border-b border-surface-border shrink-0">
          {!collapsed && (
            <span className="text-sm font-bold text-primary tracking-tight select-none">
              DFS Edge Pro
            </span>
          )}
          <button
            onClick={onToggle}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className="p-1.5 rounded-md text-text-muted hover:text-text-primary hover:bg-surface-raised transition-colors ml-auto"
          >
            {collapsed ? <Menu className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
          </button>
        </div>

        {/* Search trigger */}
        <div className="px-2 py-2 shrink-0">
          {collapsed ? (
            <button
              onClick={() => setSearchOpen(true)}
              aria-label="Open search (Ctrl+K)"
              className="w-full flex items-center justify-center p-1.5 rounded-md text-text-muted hover:text-text-primary hover:bg-surface-raised transition-colors"
            >
              <Search className="w-4 h-4" />
            </button>
          ) : (
            <button
              onClick={() => setSearchOpen(true)}
              className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md text-xs text-text-muted
                bg-surface-raised border border-surface-border hover:border-primary/40 transition-colors"
            >
              <Search className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
              <span className="flex-1 text-left">Search...</span>
              <kbd className="text-[10px] px-1 py-0.5 rounded bg-surface-border font-sans">⌘K</kbd>
            </button>
          )}
        </div>

        {/* Nav links */}
        <nav className="flex-1 py-1 overflow-y-auto scrollbar-thin" aria-label="Main navigation">
          {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
            const active = path === href || (href !== '/' && path.startsWith(href))
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? 'page' : undefined}
                title={collapsed ? label : undefined}
                className={cn(
                  'flex items-center gap-3 mx-2 px-2.5 py-2 rounded-md text-sm font-medium transition-colors duration-100',
                  'hover:bg-surface-raised hover:text-text-primary',
                  active ? 'bg-primary-muted text-primary' : 'text-text-secondary',
                  collapsed && 'justify-center px-0',
                )}
              >
                <Icon className="w-4 h-4 shrink-0" aria-hidden="true" />
                {!collapsed && <span>{label}</span>}
              </Link>
            )
          })}
        </nav>

        {/* Footer: clock + theme + notifications + avatar */}
        <div className="px-3 py-3 border-t border-surface-border shrink-0 flex flex-col gap-2">
          {!collapsed && clock && (
            <span className="text-[10px] text-text-muted text-center block select-none">
              {clock}
            </span>
          )}
          <div className={cn('flex items-center gap-2', collapsed ? 'flex-col' : 'justify-between')}>
            {mounted && (
              <button
                onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
                aria-label="Toggle color theme"
                className="p-1.5 rounded-md text-text-muted hover:text-text-primary hover:bg-surface-raised transition-colors"
              >
                {resolvedTheme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
              </button>
            )}
            <button
              aria-label="Notifications (coming soon)"
              className="p-1.5 rounded-md text-text-muted hover:text-text-primary hover:bg-surface-raised transition-colors"
            >
              <Bell className="w-4 h-4" />
            </button>
            <div
              className="w-7 h-7 rounded-full bg-primary flex items-center justify-center text-xs font-bold text-white shrink-0 select-none"
              aria-label="User avatar"
            >
              D
            </div>
          </div>
        </div>
      </aside>

      <GlobalSearch open={searchOpen} onClose={() => setSearchOpen(false)} />
    </>
  )
}
