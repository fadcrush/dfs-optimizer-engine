'use client'
import { useEffect, useState, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { Search, X } from 'lucide-react'
import { cn } from '@/lib/utils'

const ROUTE_ITEMS = [
  { label: 'Dashboard',   href: '/',            group: 'Pages' },
  { label: 'Slates',      href: '/slates',       group: 'Pages' },
  { label: 'Projections', href: '/projections',  group: 'Pages' },
  { label: 'Optimizer',   href: '/optimizer',    group: 'Pages' },
  { label: 'EV Modeling', href: '/ev-modeling',  group: 'Pages' },
  { label: 'Simulation',  href: '/simulation',   group: 'Pages' },
  { label: 'Late Swap',   href: '/late-swap',    group: 'Pages' },
  { label: 'Auth',        href: '/auth',         group: 'Pages' },
  { label: 'Analytics',   href: '/analytics',    group: 'Pages' },
  { label: 'Metrics',     href: '/metrics',      group: 'Pages' },
]

export function GlobalSearch({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const router = useRouter()

  useEffect(() => {
    if (!open) { setQuery(''); return }
    // Small delay to allow animation before focusing
    const id = setTimeout(() => inputRef.current?.focus(), 50)
    return () => clearTimeout(id)
  }, [open])

  // Escape closes; Cmd+K / Ctrl+K toggles
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && open) onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  const results = query.length < 1
    ? ROUTE_ITEMS
    : ROUTE_ITEMS.filter((i) => i.label.toLowerCase().includes(query.toLowerCase()))

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[100] bg-black/60 flex items-start justify-center pt-[15vh] p-4 animate-fadeIn"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Global search"
        className="w-full max-w-lg bg-surface-overlay border border-surface-border rounded-xl shadow-modal overflow-hidden animate-slideUp"
      >
        <div className="flex items-center gap-3 px-4 py-3 border-b border-surface-border">
          <Search className="w-4 h-4 text-text-muted shrink-0" aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search pages, players, slates…"
            className="flex-1 bg-transparent text-sm text-text-primary placeholder:text-text-muted outline-none"
            aria-label="Search"
          />
          <button onClick={onClose} aria-label="Close search" className="text-text-muted hover:text-text-primary transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>

        <ul className="max-h-72 overflow-y-auto py-2 scrollbar-thin" role="listbox" aria-label="Search results">
          {results.map((item) => (
            <li key={item.href}>
              <button
                role="option"
                aria-selected={false}
                onClick={() => { router.push(item.href); onClose() }}
                className="w-full text-left px-4 py-2 text-sm text-text-secondary hover:bg-surface-raised hover:text-text-primary transition-colors flex items-center justify-between"
              >
                <span>{item.label}</span>
                <span className="text-xs text-text-muted">{item.group}</span>
              </button>
            </li>
          ))}
          {results.length === 0 && (
            <li className="px-4 py-3 text-sm text-text-muted">No results for &ldquo;{query}&rdquo;</li>
          )}
        </ul>
      </div>
    </div>
  )
}
