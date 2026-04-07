'use client'

import { useState, useMemo, useRef, useCallback } from 'react'
import { cn } from '@/lib/utils'

// ---------------------------------------------------------------------------
// Shared types — also used by page.tsx
// ---------------------------------------------------------------------------

export type PlayerStatus = 'locked' | 'scratched' | 'excluded' | null

export interface PlayerRec {
  name: string
  /** Raw game text from the CSV (e.g. "ATL@DEN" or "7:30PM ET") */
  gameText: string
  /** True when the game has started (auto-detected at import time) */
  gameStarted: boolean
  /** How many of the user's lineups contain this player */
  lineupCount: number
  /** Total lineups imported (for exposure %) */
  totalLineups: number
}

type FilterTab = 'all' | 'affected' | 'scratched' | 'locked' | 'started'

// ---------------------------------------------------------------------------
// Status visual config
// ---------------------------------------------------------------------------

const STATUS_CONFIG: Record<
  NonNullable<PlayerStatus>,
  { rowBg: string; nameText: string; badge: string; label: string }
> = {
  locked: {
    rowBg: 'bg-[#10263d]/90',
    nameText: 'text-[#93c5fd]',
    badge: 'bg-[#163654] text-[#7dd3fc] border border-[#7dd3fc]/20',
    label: '🔒 LOCKED',
  },
  scratched: {
    rowBg: 'bg-[#34101a]/92',
    nameText: 'text-[#fecdd3]',
    badge: 'bg-[#5b1726] text-[#fecdd3] border border-[#fecdd3]/20',
    label: '❌ OUT',
  },
  excluded: {
    rowBg: 'bg-[#0f1724]/92',
    nameText: 'text-[#7c8aa2]',
    badge: 'bg-[#1a2738] text-[#7c8aa2] border border-[#7c8aa2]/15',
    label: '🚫 EXCL',
  },
}

// ---------------------------------------------------------------------------
// Exposure bar
// ---------------------------------------------------------------------------

function ExposureBar({ count, total }: { count: number; total: number }) {
  const pct = total > 0 ? (count / total) * 100 : 0
  const color =
    pct >= 60 ? '#f87171' : pct >= 35 ? '#f4b540' : pct >= 15 ? '#7dd3fc' : '#5c6c86'
  return (
    <div className="flex items-center gap-2 min-w-[80px]">
      <div className="flex-1 h-1.5 bg-[#152233] rounded-full overflow-hidden">
        <div style={{ width: `${pct}%`, backgroundColor: color }} className="h-full rounded-full" />
      </div>
      <span className="text-[11px] font-mono text-text-muted w-10 shrink-0 text-right">
        {count}/{total}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface Props {
  players: PlayerRec[]
  /** Keyed by player name lowercased */
  statuses: Map<string, PlayerStatus>
  onSetStatus: (name: string, status: PlayerStatus) => void
  onBulkAction: (action: 'lockAllStarted' | 'clearScratches' | 'clearLocks' | 'reset') => void
  /** Re-fetches injury data and auto-scratches OUT/DOUBTFUL players in current lineups */
  onRefreshInjuries?: () => Promise<void>
}

export function PlayerCommandCenter({ players, statuses, onSetStatus, onBulkAction, onRefreshInjuries }: Props) {
  const [tab, setTab] = useState<FilterTab>('all')
  const [search, setSearch] = useState('')
  const [focusedName, setFocusedName] = useState<string | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const [refreshing, setRefreshing] = useState(false)

  // Quick counts for tab badges
  const counts = useMemo(
    () =>
      players.reduce(
        (acc, p) => {
          const s = statuses.get(p.name.toLowerCase()) ?? null
          if (s === 'scratched') acc.scratched++
          if (s === 'locked') acc.locked++
          if (p.gameStarted) acc.started++
          if (s === 'scratched' || (p.gameStarted && s !== 'locked')) acc.affected++
          return acc
        },
        { scratched: 0, locked: 0, started: 0, affected: 0 },
      ),
    [players, statuses],
  )

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return players.filter(p => {
      if (q && !p.name.toLowerCase().includes(q)) return false
      const s = statuses.get(p.name.toLowerCase()) ?? null
      switch (tab) {
        case 'scratched': return s === 'scratched'
        case 'locked':    return s === 'locked'
        case 'started':   return p.gameStarted
        case 'affected':  return s === 'scratched' || (p.gameStarted && s !== 'locked')
        default:          return true
      }
    })
  }, [players, statuses, tab, search])

  // Keyboard shortcuts when a row is focused
  const handleKey = useCallback(
    (e: React.KeyboardEvent, name: string) => {
      const cur = statuses.get(name.toLowerCase()) ?? null
      if (e.key === 'l' || e.key === 'L') {
        e.preventDefault()
        onSetStatus(name, cur === 'locked' ? null : 'locked')
      } else if (e.key === 's' || e.key === 'S') {
        e.preventDefault()
        onSetStatus(name, cur === 'scratched' ? null : 'scratched')
      } else if (e.key === 'x' || e.key === 'X') {
        e.preventDefault()
        onSetStatus(name, cur === 'excluded' ? null : 'excluded')
      } else if (e.key === 'Escape') {
        e.preventDefault()
        onSetStatus(name, null)
      }
    },
    [statuses, onSetStatus],
  )

  const tabs: Array<{ id: FilterTab; label: string; badge?: number; badgeColor?: string }> = [
    { id: 'all',       label: `All`,       badge: players.length },
    { id: 'affected',  label: `⚠ At Risk`, badge: counts.affected,  badgeColor: counts.affected  > 0 ? 'text-[#fbbf24]' : undefined },
    { id: 'scratched', label: `❌ Out`,    badge: counts.scratched, badgeColor: counts.scratched > 0 ? 'text-[#f87171]' : undefined },
    { id: 'locked',    label: `🔒 Locked`, badge: counts.locked },
    { id: 'started',   label: `▶ Started`, badge: counts.started,   badgeColor: counts.started   > 0 ? 'text-[#fbbf24]' : undefined },
  ]

  return (
    <div className="glass-panel rounded-[24px] overflow-hidden">
      {/* ── Toolbar ─────────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-surface-border/80 bg-[rgba(8,14,24,0.42)] flex-wrap">
        {/* Filter tabs */}
        <div className="flex gap-1 flex-wrap">
          {tabs.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                'px-3.5 py-1.5 rounded-full text-[11px] font-bold transition-colors cursor-pointer border',
                tab === t.id
                  ? 'bg-[#4d3a13] border-[#f4b540]/35 text-[#f4b540]'
                  : 'bg-transparent border-surface-border/40 text-text-muted hover:text-text-secondary hover:border-surface-border/80',
                t.badgeColor && tab !== t.id ? t.badgeColor : '',
              )}
            >
              {t.label}
              {t.badge != null && (
                <span className="ml-1 opacity-60">{t.badge}</span>
              )}
            </button>
          ))}
        </div>

        {/* Search */}
        <input
          ref={searchRef}
          type="text"
          placeholder="Search player…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="glass-strip rounded-full px-3 py-1.5 text-sm outline-none w-44 text-text-primary placeholder:text-text-muted"
        />

        {/* Bulk actions */}
        <div className="ml-auto flex gap-1.5 flex-wrap">
          {onRefreshInjuries && (
            <button
              onClick={async () => {
                setRefreshing(true)
                await onRefreshInjuries()
                setRefreshing(false)
              }}
              disabled={refreshing}
              title="Re-fetch injury report and auto-scratch OUT/DOUBTFUL players"
              className="px-3 py-1.5 rounded-full text-[11px] font-bold cursor-pointer border border-[#fde68a]/20 bg-[#3b2a06] text-[#fde68a] hover:bg-[#4d3a13] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {refreshing ? '⟳ Checking…' : '⟳ Refresh Injuries'}
            </button>
          )}
          {counts.started > 0 && (
            <button
              onClick={() => onBulkAction('lockAllStarted')}
              title="Lock all players in games that have started"
              className="px-3 py-1.5 rounded-full text-[11px] font-bold cursor-pointer border border-[#7dd3fc]/20 bg-[#163654] text-[#7dd3fc] hover:bg-[#1b4469] transition-colors"
            >
              🔒 Lock All Started
            </button>
          )}
          {counts.scratched > 0 && (
            <button
              onClick={() => onBulkAction('clearScratches')}
              title="Clear all scratched players"
              className="px-3 py-1.5 rounded-full text-[11px] font-bold cursor-pointer border border-[#fecdd3]/20 bg-[#5b1726] text-[#fecdd3] hover:bg-[#6e1d2e] transition-colors"
            >
              Clear Scratches
            </button>
          )}
          {counts.locked > 0 && (
            <button
              onClick={() => onBulkAction('clearLocks')}
              title="Clear all manually locked players"
              className="px-3 py-1.5 rounded-full text-[11px] font-semibold cursor-pointer border border-surface-border/60 text-text-muted hover:text-text-secondary bg-transparent transition-colors"
            >
              Clear Locks
            </button>
          )}
          <button
            onClick={() => onBulkAction('reset')}
            className="px-3 py-1.5 rounded-full text-[11px] font-semibold cursor-pointer border border-surface-border/60 text-text-muted hover:text-text-secondary bg-transparent transition-colors"
          >
            Reset All
          </button>
        </div>
      </div>

      {/* ── Keyboard hint ───────────────────────────────────────────────────── */}
      <div className="px-4 py-2 bg-[rgba(7,14,24,0.42)] border-b border-surface-border/80 text-[10px] text-text-muted flex items-center gap-4 flex-wrap">
        <span className="section-label text-[10px] tracking-[0.14em]">Keyboard</span>
        {[
          { key: 'L', hint: 'lock' },
          { key: 'S', hint: 'scratch' },
          { key: 'X', hint: 'exclude' },
          { key: 'Esc', hint: 'clear' },
        ].map(({ key, hint }) => (
          <span key={key}>
            <kbd className="px-1.5 py-0.5 bg-[#162438] border border-[#3f4d64] rounded text-[10px] text-[#d7dee8] font-mono">
              {key}
            </kbd>
            <span className="ml-1 text-text-muted">{hint}</span>
          </span>
        ))}
      </div>

      {/* ── Table ───────────────────────────────────────────────────────────── */}
      <div className="overflow-y-auto scrollbar-thin" style={{ maxHeight: 'calc(100vh - 380px)' }}>
        <table className="w-full border-collapse">
          <thead className="sticky top-0 z-10">
            <tr>
              {['Player', 'Game', 'Exposure', 'Status', 'Actions'].map(h => (
                <th
                  key={h}
                  className="px-3 py-2.5 text-[10px] font-black uppercase tracking-[0.16em] text-text-muted border-b border-surface-border text-left bg-[rgba(7,14,24,0.92)] whitespace-nowrap"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-sm text-text-muted">
                  No players match this filter
                </td>
              </tr>
            )}
            {filtered.map(p => {
              const nameLower = p.name.toLowerCase()
              const status = statuses.get(nameLower) ?? null
              const cfg = status ? STATUS_CONFIG[status] : null
              const isFocused = focusedName === p.name
              const needsAttention = p.gameStarted && status === null

              return (
                <tr
                  key={p.name}
                  tabIndex={0}
                  onFocus={() => setFocusedName(p.name)}
                  onBlur={() => setFocusedName(null)}
                  onKeyDown={e => handleKey(e, p.name)}
                  className={cn(
                    'border-b border-surface-border/80 transition-colors outline-none cursor-default',
                    cfg
                      ? cfg.rowBg
                      : needsAttention
                      ? 'bg-[#3b2810]/88'
                      : 'hover:bg-white/[0.03]',
                    isFocused ? 'ring-1 ring-inset ring-[#f4b540]/40' : '',
                  )}
                >
                  {/* Player name */}
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-2">
                      <span
                        className={cn(
                          'text-sm font-semibold',
                          cfg
                            ? cfg.nameText
                            : needsAttention
                            ? 'text-[#fbbf24]'
                            : 'text-text-primary',
                        )}
                      >
                        {p.name}
                      </span>
                      {p.gameStarted && (
                        <span
                          className="w-2 h-2 rounded-full bg-[#ef4444] inline-block shrink-0 animate-pulse"
                          title="Game in progress"
                        />
                      )}
                    </div>
                  </td>

                  {/* Game */}
                  <td className="px-3 py-2.5">
                    <span
                      className={cn(
                        'text-[11px] font-mono tracking-wide',
                        p.gameStarted ? 'text-[#fbbf24]' : 'text-text-muted',
                      )}
                    >
                      {p.gameText || '—'}
                      {p.gameStarted && (
                        <span className="ml-1.5 text-[10px] text-[#ef4444] font-bold not-italic tracking-wide">
                          LIVE
                        </span>
                      )}
                    </span>
                  </td>

                  {/* Exposure */}
                  <td className="px-3 py-2.5">
                    <ExposureBar count={p.lineupCount} total={p.totalLineups} />
                  </td>

                  {/* Status badge */}
                  <td className="px-3 py-2.5 whitespace-nowrap">
                    {cfg && (
                      <span
                        className={cn('text-[10px] font-bold px-2 py-0.5 rounded', cfg.badge)}
                      >
                        {cfg.label}
                      </span>
                    )}
                    {!cfg && needsAttention && (
                        <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-[#4a3514] text-[#fbbf24] border border-[#fbbf24]/15">
                        ⚠ STARTED
                      </span>
                    )}
                  </td>

                  {/* Actions */}
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    {status !== null ? (
                      <button
                        onClick={() => onSetStatus(p.name, null)}
                        title="Clear status"
                        className="px-2.5 py-1 rounded-full border border-surface-border/60 text-[11px] font-semibold cursor-pointer bg-[rgba(7,14,24,0.58)] text-text-muted hover:text-text-secondary transition-colors"
                      >
                        ✕ Clear
                      </button>
                    ) : (
                      <div className="flex gap-1 justify-end">
                        <button
                          onClick={() => onSetStatus(p.name, 'locked')}
                          title="Lock — keep this player (L)"
                          className="px-2.5 py-1 rounded-full border border-[#7dd3fc]/20 text-[11px] font-semibold cursor-pointer bg-[#133149] text-[#7dd3fc] hover:bg-[#18405f] transition-colors"
                        >
                          🔒
                        </button>
                        <button
                          onClick={() => onSetStatus(p.name, 'scratched')}
                          title="Scratch — replace this player (S)"
                          className="px-2.5 py-1 rounded-full border border-[#fecdd3]/20 text-[11px] font-semibold cursor-pointer bg-[#4b1521] text-[#fecdd3] hover:bg-[#612030] transition-colors"
                        >
                          ❌
                        </button>
                        <button
                          onClick={() => onSetStatus(p.name, 'excluded')}
                          title="Exclude from candidate pool (X)"
                          className="px-2.5 py-1 rounded-full border border-[#506177] text-[11px] font-semibold cursor-pointer bg-[#1a2738] text-[#9aa8bc] hover:text-text-primary transition-colors"
                        >
                          🚫
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
