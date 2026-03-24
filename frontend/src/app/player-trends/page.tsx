'use client'

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
} from 'react'
import {
  Search,
  TrendingUp,
  User,
  BarChart2,
  AlertCircle,
  ChevronDown,
  Clock,
  Target,
  Shuffle,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { PageContainer } from '@/components/layout/PageContainer'
import { Card, CardHeader, CardBody } from '@/components/ui/Card'
import {
  searchPlayers,
  getPlayerTrends,
  getSlateTrends,
  type PlayerSearchResult,
  type PlayerTrendsResult,
  type TrendSplit,
  type GameLogRow,
  type SlatePlayerTrend,
} from '@/lib/api/player_trends'

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function fmt(v: number | null | undefined, dec = 1): string {
  if (v == null) return '—'
  return v.toFixed(dec)
}

function fmtDate(s: string | null | undefined): string {
  if (!s) return '—'
  try {
    return new Date(s).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
    })
  } catch {
    return s
  }
}

// ---------------------------------------------------------------------------
// Comparison table: stat rows (label + last5 col + last10 col + season col)
// ---------------------------------------------------------------------------

type StatRow = {
  label:    string
  key:      keyof TrendSplit
  dec?:     number
  highlight?: boolean
}

const STAT_ROWS: StatRow[] = [
  { label: 'Games',       key: 'games_used',         dec: 0 },
  { label: 'MIN',         key: 'avg_minutes',         dec: 1 },
  { label: 'PTS',         key: 'avg_points',          dec: 1, highlight: true },
  { label: 'REB',         key: 'avg_rebounds',        dec: 1 },
  { label: 'AST',         key: 'avg_assists',         dec: 1 },
  { label: 'STL',         key: 'avg_steals',          dec: 1 },
  { label: 'BLK',         key: 'avg_blocks',          dec: 1 },
  { label: 'TO',          key: 'avg_turnovers',       dec: 1 },
  { label: '3PM',         key: 'avg_three_pm',        dec: 1 },
  { label: 'FGM',         key: 'avg_fgm',             dec: 1 },
  { label: 'FTM',         key: 'avg_ftm',             dec: 1 },
  { label: 'DK Pts',      key: 'avg_dk_points',       dec: 1, highlight: true },
  { label: 'FD Pts',      key: 'avg_fd_points',       dec: 1, highlight: true },
  { label: 'DK/Min',      key: 'dk_points_per_min',   dec: 2 },
  { label: 'FD/Min',      key: 'fd_points_per_min',   dec: 2 },
  { label: 'DK StDev',    key: 'stddev_dk_points',    dec: 2 },
  { label: 'FD StDev',    key: 'stddev_fd_points',    dec: 2 },
]

const GAME_LOG_COLS = [
  { key: 'game_date',      label: 'Date',  fmt: (r: GameLogRow) => fmtDate(r.game_date) },
  { key: 'opp',            label: 'Opp',   fmt: (r: GameLogRow) => r.opponent ?? '—' },
  { key: 'location',       label: 'H/A',   fmt: (r: GameLogRow) => r.is_home == null ? '—' : r.is_home ? 'H' : '@' },
  { key: 'wl',             label: 'W/L',   fmt: (r: GameLogRow) => r.wl ?? '—' },
  { key: 'minutes',        label: 'MIN',   fmt: (r: GameLogRow) => fmt(r.minutes, 0) },
  { key: 'points',         label: 'PTS',   fmt: (r: GameLogRow) => fmt(r.points,  0) },
  { key: 'rebounds',       label: 'REB',   fmt: (r: GameLogRow) => fmt(r.rebounds, 0) },
  { key: 'assists',        label: 'AST',   fmt: (r: GameLogRow) => fmt(r.assists,  0) },
  { key: 'steals',         label: 'STL',   fmt: (r: GameLogRow) => fmt(r.steals,   0) },
  { key: 'blocks',         label: 'BLK',   fmt: (r: GameLogRow) => fmt(r.blocks,   0) },
  { key: 'turnovers',      label: 'TO',    fmt: (r: GameLogRow) => fmt(r.turnovers, 0) },
  { key: 'three_pointers', label: '3PM',   fmt: (r: GameLogRow) => fmt(r.three_pointers, 0) },
  { key: 'fg_made',        label: 'FGM',   fmt: (r: GameLogRow) => fmt(r.fg_made,  0) },
  { key: 'ft_made',        label: 'FTM',   fmt: (r: GameLogRow) => fmt(r.ft_made,  0) },
  { key: 'dk_points',      label: 'DK',    fmt: (r: GameLogRow) => fmt(r.dk_points, 1) },
  { key: 'fd_points',      label: 'FD',    fmt: (r: GameLogRow) => fmt(r.fd_points, 1) },
] as const

// ---------------------------------------------------------------------------
// Mini summary card
// ---------------------------------------------------------------------------

function SummaryCard({
  label,
  split,
  color,
}: {
  label: string
  split: TrendSplit | null | undefined
  color: string
}) {
  return (
    <div className={cn('rounded-xl border p-4 flex flex-col gap-2 min-w-0', color)}>
      <div className="text-[10px] font-bold uppercase tracking-widest text-text-muted/80">
        {label}
        {split && (
          <span className="ml-2 normal-case text-text-muted font-normal">
            ({split.games_used}g)
          </span>
        )}
      </div>
      {!split ? (
        <div className="text-xs text-text-muted italic">No data</div>
      ) : (
        <div className="grid grid-cols-2 gap-x-4 gap-y-1">
          <Stat label="DK" value={fmt(split.avg_dk_points)} large />
          <Stat label="FD" value={fmt(split.avg_fd_points)} large />
          <Stat label="MIN" value={fmt(split.avg_minutes)} />
          <Stat label="PTS" value={fmt(split.avg_points)} />
          <Stat label="REB" value={fmt(split.avg_rebounds)} />
          <Stat label="AST" value={fmt(split.avg_assists)} />
          <Stat label="DK σ" value={fmt(split.stddev_dk_points, 2)} />
          <Stat label="FD σ" value={fmt(split.stddev_fd_points, 2)} />
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, large }: { label: string; value: string; large?: boolean }) {
  return (
    <div className="flex flex-col">
      <span className="text-[9px] uppercase tracking-wide text-text-muted font-semibold">{label}</span>
      <span className={cn('font-bold', large ? 'text-base text-text-primary' : 'text-sm text-text-secondary')}>
        {value}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Player search input + dropdown
// ---------------------------------------------------------------------------

function PlayerSearch({
  value,
  onSelect,
  onClear,
}: {
  value: string
  onSelect: (p: PlayerSearchResult) => void
  onClear: () => void
}) {
  const [query, setQuery]         = useState(value)
  const [results, setResults]     = useState<PlayerSearchResult[]>([])
  const [open, setOpen]           = useState(false)
  const [searching, setSearching] = useState(false)
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  // Sync external clear
  useEffect(() => {
    if (!value) setQuery('')
  }, [value])

  // Debounced search
  useEffect(() => {
    if (debounce.current) clearTimeout(debounce.current)
    if (query.trim().length < 2) {
      setResults([])
      setOpen(false)
      return
    }
    debounce.current = setTimeout(async () => {
      setSearching(true)
      try {
        const r = await searchPlayers(query)
        setResults(r)
        setOpen(r.length > 0)
      } catch {
        setResults([])
      } finally {
        setSearching(false)
      }
    }, 300)
  }, [query])

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  function handleChange(e: ChangeEvent<HTMLInputElement>) {
    setQuery(e.target.value)
    if (!e.target.value) onClear()
  }

  function handleSelect(p: PlayerSearchResult) {
    setQuery(p.player_name)
    setOpen(false)
    onSelect(p)
  }

  return (
    <div ref={containerRef} className="relative w-full max-w-md">
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted pointer-events-none" />
        <input
          type="text"
          placeholder="Search player name…"
          value={query}
          onChange={handleChange}
          className={cn(
            'w-full pl-9 pr-4 py-2.5 rounded-lg text-sm',
            'bg-surface-overlay border border-surface-border',
            'text-text-primary placeholder:text-text-muted',
            'focus:outline-none focus:border-primary/60 transition-colors',
          )}
        />
        {searching && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted text-xs">…</div>
        )}
      </div>

      {open && results.length > 0 && (
        <div className="absolute z-50 mt-1 w-full bg-surface-raised border border-surface-border rounded-lg shadow-modal overflow-hidden">
          {results.map((p) => (
            <button
              key={p.player_name}
              onMouseDown={(e) => { e.preventDefault(); handleSelect(p) }}
              className={cn(
                'w-full text-left px-4 py-2.5 text-sm flex items-center justify-between',
                'hover:bg-surface-overlay transition-colors',
              )}
            >
              <span className="font-medium text-text-primary">{p.player_name}</span>
              <span className="text-[11px] text-text-muted">{p.team ?? '—'}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Comparison / splits table
// ---------------------------------------------------------------------------

function SplitsTable({ last5, last10, season }: {
  last5:  TrendSplit | null | undefined
  last10: TrendSplit | null | undefined
  season: TrendSplit | null | undefined
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="text-left border-b border-surface-border">
            <th className="py-2 pr-4 text-[11px] font-semibold uppercase tracking-wide text-text-muted w-24">Stat</th>
            <th className="py-2 px-3 text-[11px] font-semibold uppercase tracking-wide text-primary/80 text-center">Last 5</th>
            <th className="py-2 px-3 text-[11px] font-semibold uppercase tracking-wide text-primary text-center">Last 10</th>
            <th className="py-2 px-3 text-[11px] font-semibold uppercase tracking-wide text-text-muted text-center">Season</th>
          </tr>
        </thead>
        <tbody>
          {STAT_ROWS.map((row, idx) => (
            <tr
              key={row.key}
              className={cn(
                'border-b border-surface-border/40',
                idx % 2 === 0 && 'bg-surface-overlay/30',
                row.highlight && 'bg-primary/5',
              )}
            >
              <td className={cn(
                'py-2 pr-4 font-semibold text-[11px] uppercase tracking-wide',
                row.highlight ? 'text-primary' : 'text-text-muted',
              )}>
                {row.label}
              </td>
              <SplitCell split={last5}  row={row} />
              <SplitCell split={last10} row={row} strong />
              <SplitCell split={season} row={row} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SplitCell({
  split,
  row,
  strong,
}: {
  split: TrendSplit | null | undefined
  row: StatRow
  strong?: boolean
}) {
  const raw = split ? split[row.key] : null
  const val = raw != null ? Number(raw).toFixed(row.dec ?? 1) : '—'
  return (
    <td className={cn(
      'py-2 px-3 text-center tabular-nums',
      strong ? 'font-semibold text-text-primary' : 'text-text-secondary',
      raw == null && 'text-text-muted',
    )}>
      {val}
    </td>
  )
}

// ---------------------------------------------------------------------------
// Recent game log table
// ---------------------------------------------------------------------------

function GameLogTable({ games }: { games: GameLogRow[] }) {
  if (!games.length) {
    return <p className="text-sm text-text-muted italic px-4 py-3">No game logs available.</p>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs border-collapse min-w-[900px]">
        <thead>
          <tr className="border-b border-surface-border">
            {GAME_LOG_COLS.map((col) => (
              <th
                key={col.key}
                className={cn(
                  'py-2 px-2 text-[10px] font-semibold uppercase tracking-wide text-text-muted',
                  col.key === 'dk_points' || col.key === 'fd_points'
                    ? 'text-primary/80'
                    : '',
                  col.key === 'game_date' ? 'text-left' : 'text-center',
                )}
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {games.map((row, i) => (
            <tr
              key={row.game_id ?? i}
              className={cn(
                'border-b border-surface-border/30 transition-colors hover:bg-surface-overlay/60',
                i % 2 === 0 && 'bg-surface-overlay/20',
              )}
            >
              {GAME_LOG_COLS.map((col) => {
                const val = col.fmt(row)
                const isDfs = col.key === 'dk_points' || col.key === 'fd_points'
                const isWin = col.key === 'wl' && val === 'W'
                const isLoss = col.key === 'wl' && val === 'L'
                return (
                  <td
                    key={col.key}
                    className={cn(
                      'py-1.5 px-2 tabular-nums',
                      col.key === 'game_date' ? 'text-left' : 'text-center',
                      isDfs && 'font-semibold text-primary',
                      isWin  && 'text-emerald-400',
                      isLoss && 'text-red-400',
                    )}
                  >
                    {val}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Slate-wide table
// ---------------------------------------------------------------------------

type SortKey = keyof SlatePlayerTrend
type SortDir = 'asc' | 'desc'

function SlateTable({ players }: { players: SlatePlayerTrend[] }) {
  const [sortKey, setSortKey] = useState<SortKey>('avg_pts')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir(d => (d === 'desc' ? 'asc' : 'desc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const sorted = [...players].sort((a, b) => {
    const av = a[sortKey]
    const bv = b[sortKey]
    if (typeof av === 'string' && typeof bv === 'string') {
      return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av)
    }
    const an = (av as number) ?? 0
    const bn = (bv as number) ?? 0
    return sortDir === 'asc' ? an - bn : bn - an
  })

  const COLS: { key: SortKey; label: string }[] = [
    { key: 'player_name', label: 'Player' },
    { key: 'team',        label: 'Team'  },
    { key: 'games_l10',   label: 'G'     },
    { key: 'avg_min',     label: 'MIN'   },
    { key: 'avg_pts',     label: 'PTS'   },
    { key: 'avg_reb',     label: 'REB'   },
    { key: 'avg_ast',     label: 'AST'   },
    { key: 'avg_stl',     label: 'STL'   },
    { key: 'avg_blk',     label: 'BLK'   },
    { key: 'avg_tov',     label: 'TO'    },
    { key: 'avg_3pm',     label: '3PM'   },
  ]

  if (!players.length) {
    return <p className="text-sm text-text-muted italic px-4 py-4">No players found.</p>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b border-surface-border">
            {COLS.map((col) => (
              <th
                key={col.key}
                onClick={() => toggleSort(col.key)}
                className={cn(
                  'py-2 px-3 text-[11px] font-semibold uppercase tracking-wide cursor-pointer select-none',
                  'hover:text-primary transition-colors',
                  col.key === 'player_name' ? 'text-left' : 'text-center',
                  sortKey === col.key ? 'text-primary' : 'text-text-muted',
                )}
              >
                {col.label}
                {sortKey === col.key && (
                  <span className="ml-1">{sortDir === 'desc' ? '↓' : '↑'}</span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((p, i) => (
            <tr
              key={p.player_name}
              className={cn(
                'border-b border-surface-border/30 hover:bg-surface-overlay/60 transition-colors cursor-pointer',
                i % 2 === 0 && 'bg-surface-overlay/20',
              )}
            >
              <td className="py-2 px-3 font-medium text-text-primary">{p.player_name}</td>
              <td className="py-2 px-3 text-center text-text-secondary text-xs">{p.team ?? '—'}</td>
              <td className="py-2 px-3 text-center tabular-nums text-text-secondary">{p.games_l10}</td>
              <td className="py-2 px-3 text-center tabular-nums">{fmt(p.avg_min)}</td>
              <td className="py-2 px-3 text-center tabular-nums font-semibold text-text-primary">{fmt(p.avg_pts)}</td>
              <td className="py-2 px-3 text-center tabular-nums">{fmt(p.avg_reb)}</td>
              <td className="py-2 px-3 text-center tabular-nums">{fmt(p.avg_ast)}</td>
              <td className="py-2 px-3 text-center tabular-nums">{fmt(p.avg_stl)}</td>
              <td className="py-2 px-3 text-center tabular-nums">{fmt(p.avg_blk)}</td>
              <td className="py-2 px-3 text-center tabular-nums text-red-400/80">{fmt(p.avg_tov)}</td>
              <td className="py-2 px-3 text-center tabular-nums">{fmt(p.avg_3pm)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function Skeleton({ className }: { className?: string }) {
  return (
    <div className={cn('animate-pulse rounded bg-surface-overlay/60', className)} />
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

type Tab = 'player' | 'slate'

export default function PlayerTrendsPage() {
  const [tab, setTab]                   = useState<Tab>('player')
  const [selectedPlayer, setSelectedPlayer] = useState<string>('')
  const [trends, setTrends]             = useState<PlayerTrendsResult | null>(null)
  const [loading, setLoading]           = useState(false)
  const [error, setError]               = useState<string | null>(null)

  // Slate view
  const [slateData, setSlateData]       = useState<SlatePlayerTrend[]>([])
  const [slateLoading, setSlateLoading] = useState(false)
  const [slateLoaded, setSlateLoaded]   = useState(false)

  // ------------------------------------------------------------------
  // Player selection → fetch trends
  // ------------------------------------------------------------------

  const handlePlayerSelect = useCallback(async (p: PlayerSearchResult) => {
    setSelectedPlayer(p.player_name)
    setTrends(null)
    setError(null)
    setLoading(true)
    try {
      const data = await getPlayerTrends(p.player_name)
      if (data === null) {
        setError(`No game logs found for "${p.player_name}". The player may not have qualifying data in the current database.`)
      } else {
        setTrends(data)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load player trends.')
    } finally {
      setLoading(false)
    }
  }, [])

  function handleClear() {
    setSelectedPlayer('')
    setTrends(null)
    setError(null)
  }

  // ------------------------------------------------------------------
  // Slate table load (lazy — only when tab switched)
  // ------------------------------------------------------------------

  useEffect(() => {
    if (tab !== 'slate' || slateLoaded) return
    setSlateLoading(true)
    getSlateTrends(300)
      .then((d) => { setSlateData(d); setSlateLoaded(true) })
      .catch(() => setSlateData([]))
      .finally(() => setSlateLoading(false))
  }, [tab, slateLoaded])

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  return (
    <PageContainer
      title="Player Trends"
      description="Last-10 game log analytics for NBA DFS research. All averages are historical descriptive statistics — not projections."
      breadcrumbs={[{ label: 'Research' }, { label: 'Player Trends' }]}
    >
      {/* ── Disclaimer banner ── */}
      <div className="mb-5 flex items-start gap-2 bg-amber-900/20 border border-amber-700/30 rounded-lg px-4 py-3 text-[12px] text-amber-300/90">
        <AlertCircle className="w-4 h-4 mt-0.5 shrink-0 text-amber-400" />
        <span>
          <strong>Research tool:</strong> All averages shown are computed from actual box-score logs.
          They are historical statistics, not forward-looking projections. Use them to inform —
          not replace — your lineup decisions.
        </span>
      </div>

      {/* ── Tab switcher ── */}
      <div className="flex gap-2 mb-5">
        {([['player', 'Player Lookup', User], ['slate', 'Slate Overview', BarChart2]] as const).map(
          ([id, label, Icon]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={cn(
                'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors',
                tab === id
                  ? 'bg-primary text-white'
                  : 'bg-surface-overlay text-text-secondary hover:text-text-primary border border-surface-border',
              )}
            >
              <Icon className="w-4 h-4" />
              {label}
            </button>
          ),
        )}
      </div>

      {/* ── PLAYER LOOKUP tab ── */}
      {tab === 'player' && (
        <div className="space-y-5">
          {/* Search bar */}
          <Card variant="bordered">
            <CardBody className="flex items-center gap-4 flex-wrap">
              <PlayerSearch
                value={selectedPlayer}
                onSelect={handlePlayerSelect}
                onClear={handleClear}
              />
              {selectedPlayer && (
                <button
                  onClick={handleClear}
                  className="text-xs text-text-muted hover:text-text-primary underline transition-colors"
                >
                  Clear
                </button>
              )}
              {!selectedPlayer && (
                <span className="text-xs text-text-muted">
                  Type at least 2 characters to search
                </span>
              )}
            </CardBody>
          </Card>

          {/* Loading state */}
          {loading && (
            <div className="space-y-4">
              <div className="grid grid-cols-3 gap-4">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-[140px]" />
                ))}
              </div>
              <Skeleton className="h-64" />
              <Skeleton className="h-48" />
            </div>
          )}

          {/* Error state */}
          {!loading && error && (
            <div className="flex items-start gap-3 bg-red-900/20 border border-red-700/30 rounded-lg px-4 py-4 text-sm text-red-300">
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0 text-red-400" />
              {error}
            </div>
          )}

          {/* Empty / prompt state */}
          {!loading && !error && !trends && (
            <div className="flex flex-col items-center justify-center py-16 gap-3 text-text-muted">
              <TrendingUp className="w-10 h-10 opacity-30" />
              <p className="text-sm">Search for a player to view their last-10 analytics</p>
            </div>
          )}

          {/* Results */}
          {!loading && !error && trends && (
            <>
              {/* Player identity header */}
              <div className="flex items-center gap-3 px-1">
                <div className="w-10 h-10 rounded-full bg-primary/20 border border-primary/30 flex items-center justify-center">
                  <User className="w-5 h-5 text-primary" />
                </div>
                <div>
                  <h2 className="text-xl font-bold text-text-primary">{trends.player_name}</h2>
                  <div className="flex items-center gap-3 text-xs text-text-muted mt-0.5">
                    <span>{trends.team ?? 'Unknown Team'}</span>
                    {trends.current_season && (
                      <><span>·</span><span>Season: {trends.current_season}</span></>
                    )}
                    <span>·</span>
                    <span>{trends.total_games_available} games in log</span>
                  </div>
                </div>
              </div>

              {/* Summary cards */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <SummaryCard
                  label="Last 5"
                  split={trends.last5}
                  color="bg-surface-overlay/60 border border-surface-border"
                />
                <SummaryCard
                  label="Last 10"
                  split={trends.last10}
                  color="bg-primary/5 border border-primary/20"
                />
                <SummaryCard
                  label="Season"
                  split={trends.season}
                  color="bg-surface-overlay/60 border border-surface-border"
                />
              </div>

              {/* Splits comparison table */}
              <Card variant="bordered">
                <CardHeader
                  title="Split Comparison — Last 5 / Last 10 / Season"
                  icon={<Shuffle className="w-4 h-4" />}
                />
                <CardBody className="p-0 pb-2">
                  <SplitsTable
                    last5={trends.last5}
                    last10={trends.last10}
                    season={trends.season}
                  />
                </CardBody>
              </Card>

              {/* Recent game log */}
              <Card variant="bordered">
                <CardHeader
                  title={`Recent Game Log (Last ${trends.recent_games.length})`}
                  icon={<Clock className="w-4 h-4" />}
                  action={
                    <span className="text-[10px] text-text-muted uppercase tracking-wide">
                      DK/FD computed from raw stats
                    </span>
                  }
                />
                <CardBody className="p-0 pb-2">
                  <GameLogTable games={trends.recent_games} />
                </CardBody>
              </Card>

              {/* Per-minute efficiency note */}
              {(trends.last10?.dk_points_per_min != null) && (
                <Card variant="bordered">
                  <CardHeader
                    title="Per-Minute Efficiency (Last 10)"
                    icon={<Target className="w-4 h-4" />}
                  />
                  <CardBody className="flex flex-wrap gap-6">
                    {[
                      { label: 'PTS/Min',    v: trends.last10?.points_per_min },
                      { label: 'REB/Min',    v: trends.last10?.rebounds_per_min },
                      { label: 'AST/Min',    v: trends.last10?.assists_per_min },
                      { label: 'DK Pts/Min', v: trends.last10?.dk_points_per_min },
                      { label: 'FD Pts/Min', v: trends.last10?.fd_points_per_min },
                    ].map(({ label, v }) => (
                      <div key={label} className="flex flex-col items-center gap-1 min-w-[80px]">
                        <span className="text-[10px] uppercase tracking-wide text-text-muted font-semibold">
                          {label}
                        </span>
                        <span className="text-lg font-bold text-text-primary tabular-nums">
                          {fmt(v, 3)}
                        </span>
                      </div>
                    ))}
                  </CardBody>
                </Card>
              )}
            </>
          )}
        </div>
      )}

      {/* ── SLATE OVERVIEW tab ── */}
      {tab === 'slate' && (
        <Card variant="bordered">
          <CardHeader
            title="Slate Overview — Last-10 Averages"
            icon={<BarChart2 className="w-4 h-4" />}
            action={
              <span className="text-[10px] text-text-muted">
                Click column headers to sort · Min 3 games required
              </span>
            }
          />
          <CardBody className="p-0 pb-2">
            {slateLoading ? (
              <div className="space-y-2 p-4">
                {Array.from({ length: 8 }).map((_, i) => (
                  <Skeleton key={i} className="h-8" />
                ))}
              </div>
            ) : (
              <SlateTable players={slateData} />
            )}
          </CardBody>
        </Card>
      )}
    </PageContainer>
  )
}
