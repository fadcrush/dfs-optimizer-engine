'use client'

import React, { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { authFetch } from '@/lib/auth'
import { cn } from '@/lib/utils'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Game {
  id: string
  home_team: string
  away_team: string
  home_abbr: string
  away_abbr: string
  time: string
  spread_home: number | null
  total: number | null
  home_ml: number | null
  away_ml: number | null
}

interface Slate {
  id: string
  platform: string
  sport: string
  date: string
  player_count: number | null
  created_at: string
  status: string
  file_name: string
  teams?: string[]
}

interface Player {
  name: string
  position: string
  position_raw: string
  team: string
  opponent: string
  salary: number
  fppg: number
  value: number
  injury: string
  game: string
}

const fetch = authFetch

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'
const EXCLUDED_KEY         = 'dfs_excluded_teams'
const EXCLUDED_PLAYERS_KEY = 'dfs_excluded_players'

const TEAM_COLORS: Record<string, string> = {
  BOS: '#007A33', LAL: '#552583', GSW: '#006BB6', MIA: '#98002E',
  CHI: '#CE1141', NYK: '#006BB6', BKN: '#000000', PHI: '#006BB6',
  TOR: '#CE1141', MIL: '#00471B', IND: '#FDBB30', CLE: '#860038',
  DET: '#C8102E', ORL: '#0077C0', ATL: '#E03A3E', CHA: '#1D1160',
  DEN: '#0E2240', OKC: '#007AC1', MIN: '#0C2340', DAL: '#00538C',
  HOU: '#CE1141', SAS: '#C4CED4', NOP: '#0C2340', MEM: '#5D76A9',
  UTA: '#002B5C', PHX: '#1D1160', SAC: '#5A2D81', LAC: '#C8102E',
  POR: '#E03A3E', WAS: '#002B5C',
}

function teamColor(abbr: string): string {
  return TEAM_COLORS[abbr] ?? '#1e3a5f'
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtSpread(val: number | null): string {
  if (val === null) return 'N/A'
  return val > 0 ? `+${val}` : String(val)
}

function fmtML(val: number | null): string {
  if (val === null) return 'N/A'
  return val > 0 ? `+${val}` : String(val)
}

const btnBlueCls  = 'px-3 py-[6px] text-xs font-semibold cursor-pointer rounded-full bg-[#163654] text-[#7dd3fc] border border-[#7dd3fc]/20'
const btnRedCls   = 'px-3 py-[6px] text-xs font-semibold cursor-pointer rounded-full bg-[#5b1726] text-[#fecdd3] border border-[#fecdd3]/20'
const btnGrayCls  = 'px-3 py-[6px] text-[11px] font-semibold cursor-pointer rounded-full glass-strip text-text-secondary border border-surface-border/50'
const btnDangerCls = 'px-3 py-1 text-[11px] font-semibold cursor-pointer rounded-full bg-[#5b1726] text-[#fecdd3] border border-[#fecdd3]/20'

// ---------------------------------------------------------------------------
// GameCard
// ---------------------------------------------------------------------------

function GameCard({ game }: { game: Game }) {
  const homeSpread = fmtSpread(game.spread_home)
  const awaySpread = fmtSpread(game.spread_home !== null ? -game.spread_home : null)
  const over = game.total !== null ? `O/U ${game.total}` : 'O/U N/A'

  return (
    <div className="shrink-0 w-52 glass-panel rounded-2xl px-3 py-3">
      <div className="text-[10px] text-text-muted mb-[7px] text-center uppercase tracking-[0.12em] font-bold">
        {game.time}
      </div>

      {/* Away */}
      <div className="flex justify-between items-center mb-[5px]">
        <div className="flex items-center gap-1.5">
          <div className="w-7 h-7 rounded-[5px] flex items-center justify-center text-[9px] font-extrabold text-white bg-[var(--tc)]" style={{'--tc': teamColor(game.away_abbr)} as React.CSSProperties}>
            {game.away_abbr.slice(0, 3)}
          </div>
          <span className="text-[11px] text-text-primary font-semibold">{game.away_abbr}</span>
        </div>
        <span className="text-[11px] text-text-primary font-mono">{awaySpread}</span>
        <span className="text-[11px] text-text-muted font-mono min-w-[40px] text-right">{fmtML(game.away_ml)}</span>
      </div>

      {/* Home */}
      <div className="flex justify-between items-center">
        <div className="flex items-center gap-1.5">
          <div className="w-7 h-7 rounded-[5px] flex items-center justify-center text-[9px] font-extrabold text-white bg-[var(--tc)]" style={{'--tc': teamColor(game.home_abbr)} as React.CSSProperties}>
            {game.home_abbr.slice(0, 3)}
          </div>
          <span className="text-[11px] text-text-primary font-semibold">{game.home_abbr}</span>
        </div>
        <span className="text-[11px] text-text-primary font-mono">{homeSpread}</span>
        <span className="text-[11px] text-text-muted font-mono min-w-[40px] text-right">{fmtML(game.home_ml)}</span>
      </div>

      {/* Total */}
      <div className="mt-2 border-t border-surface-border/60 pt-1.5 text-center text-[10px] text-[#f4b540] font-semibold tracking-[0.08em] uppercase">
        {over}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Column + position meta
// ---------------------------------------------------------------------------

type ColKey = 'salary' | 'fppg' | 'value' | 'injury'
const ALL_COLS: { key: ColKey; label: string }[] = [
  { key: 'salary',  label: 'Salary' },
  { key: 'fppg',   label: 'FPPG'   },
  { key: 'value',  label: 'Value'  },
  { key: 'injury', label: 'Status' },
]

const POSITIONS = ['ALL', 'PG', 'SG', 'SF', 'PF', 'C'] as const
type PosFilter = (typeof POSITIONS)[number]

// ---------------------------------------------------------------------------
// DashboardPage
// ---------------------------------------------------------------------------

export default function DashboardPage() {
  const router = useRouter()

  // Games
  const [games, setGames]               = useState<Game[]>([])
  const [gamesSource, setGamesSource]   = useState('')
  const [gamesDate, setGamesDate]       = useState('')
  const [gamesLoading, setGamesLoading] = useState(true)

  // Slates + teams
  const [slates, setSlates]                   = useState<Slate[]>([])
  const [teams, setTeams]                     = useState<string[]>([])
  const [excludedTeams, setExcludedTeams]     = useState<Set<string>>(new Set())
  const [slatesLoading, setSlatesLoading]     = useState(true)

  // Players
  const [players, setPlayers]                 = useState<Player[]>([])
  const [playersLoading, setPlayersLoading]   = useState(false)
  // Individually excluded players (persisted to localStorage)
  const [excludedPlayers, setExcludedPlayers] = useState<Set<string>>(new Set())
  // Player table UI
  const [search, setSearch]                   = useState('')
  const [posFilter, setPosFilter]             = useState<PosFilter>('ALL')
  const [visibleCols, setVisibleCols]         = useState<Set<ColKey>>(new Set(['salary', 'fppg', 'value'] as ColKey[]))
  const [showColMenu, setShowColMenu]         = useState(false)
  const [sortCol, setSortCol]                 = useState<'name' | 'salary' | 'fppg' | 'value'>('salary')
  const [sortDir, setSortDir]                 = useState<'asc' | 'desc'>('desc')
  const [rowsPerPage, setRowsPerPage]         = useState(25)
  const [tablePage, setTablePage]             = useState(0)

  // ---- Restore excluded teams + players from localStorage ----
  useEffect(() => {
    try {
      const saved = localStorage.getItem(EXCLUDED_KEY)
      if (saved) setExcludedTeams(new Set(JSON.parse(saved) as string[]))
    } catch { /* ignore */ }
    try {
      const saved = localStorage.getItem(EXCLUDED_PLAYERS_KEY)
      if (saved) setExcludedPlayers(new Set(JSON.parse(saved) as string[]))
    } catch { /* ignore */ }
  }, [])

  // ---- Fetch today's games ----
  useEffect(() => {
    fetch(`${API_BASE}/api/games/today`)
      .then(r => r.json())
      .then(data => {
        setGames(data.games ?? [])
        setGamesSource(data.source ?? '')
        setGamesDate(data.date ?? '')
      })
      .catch(() => {})
      .finally(() => setGamesLoading(false))
  }, [])

  // ---- Fetch slates & extract teams + players ----
  useEffect(() => {
    fetch(`${API_BASE}/api/slates`)
      .then(r => r.json())
      .then(data => {
        const all: Slate[] = data.slates ?? []
        setSlates(all)
        if (all.length > 0) {
          const latest = [...all].sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))[0]
          setTeams(latest.teams ?? [])
          // Fetch players for this slate
          setPlayersLoading(true)
          fetch(`${API_BASE}/api/slates/${latest.id}/players`)
            .then(r => r.json())
            .then(d => setPlayers(d.players ?? []))
            .catch(() => {})
            .finally(() => setPlayersLoading(false))
        }
      })
      .catch(() => {})
      .finally(() => setSlatesLoading(false))
  }, [])

  const toggleTeam = (abbr: string) => {
    setExcludedTeams(prev => {
      const next = new Set(prev)
      next.has(abbr) ? next.delete(abbr) : next.add(abbr)
      localStorage.setItem(EXCLUDED_KEY, JSON.stringify([...next]))
      return next
    })
  }

  const includeAll = () => {
    setExcludedTeams(new Set())
    localStorage.setItem(EXCLUDED_KEY, JSON.stringify([]))
  }

  const fadeAll = () => {
    setExcludedTeams(new Set(teams))
    localStorage.setItem(EXCLUDED_KEY, JSON.stringify(teams))
  }

  const excludedCount = excludedTeams.size
  const latestSlate = slates.length > 0
    ? [...slates].sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))[0]
    : null

  const togglePlayer = (name: string) => {
    setExcludedPlayers(prev => {
      const next = new Set(prev)
      next.has(name) ? next.delete(name) : next.add(name)
      localStorage.setItem(EXCLUDED_PLAYERS_KEY, JSON.stringify([...next]))
      return next
    })
  }
  const clearExcludedPlayers = () => {
    setExcludedPlayers(new Set())
    localStorage.setItem(EXCLUDED_PLAYERS_KEY, '[]')
  }

  // ---- Player table helpers ----
  const toggleCol = (k: ColKey) => {
    setVisibleCols(prev => { const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); return n })
  }

  const handleSort = (col: typeof sortCol) => {
    if (col === sortCol) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortCol(col); setSortDir('desc') }
    setTablePage(0)
  }

  const sortIcon = (col: typeof sortCol) => {
    if (col !== sortCol) return <span className="text-[9px] text-[#475569]"> ⇅</span>
    return <span className="text-[9px] text-[#60a5fa]">{sortDir === 'asc' ? ' ↑' : ' ↓'}</span>
  }

  // Filtered + sorted player list
  const filteredPlayers = players
    .filter(p => {
      if (posFilter !== 'ALL') {
        const parts = (p.position_raw || p.position).toUpperCase().split('/')
        if (!parts.includes(posFilter as string)) return false
      }
      if (search) {
        const q = search.toLowerCase()
        if (!p.name.toLowerCase().includes(q) &&
            !p.team.toLowerCase().includes(q) &&
            !p.opponent.toLowerCase().includes(q)) return false
      }
      return true
    })
    .sort((a, b) => {
      let cmp = 0
      if      (sortCol === 'name')   cmp = a.name.localeCompare(b.name)
      else if (sortCol === 'salary') cmp = a.salary - b.salary
      else if (sortCol === 'fppg')   cmp = a.fppg   - b.fppg
      else if (sortCol === 'value')  cmp = a.value  - b.value
      return sortDir === 'asc' ? cmp : -cmp
    })

  const totalPages = Math.ceil(filteredPlayers.length / rowsPerPage)
  const pageRows   = filteredPlayers.slice(tablePage * rowsPerPage, (tablePage + 1) * rowsPerPage)

  return (
    <div className="min-h-screen bg-surface-base text-text-primary font-sans px-3 py-3 md:px-4 md:py-5">
      <div className="max-w-[1400px] mx-auto glass-page rounded-[30px] px-4 py-5 md:px-6 md:py-6">

        {/* ========== Header ========== */}
        <div className="flex justify-between items-start mb-6 flex-wrap gap-3">
          <div>
            <div className="section-label text-[#f4b540] mb-2">Today&apos;s Board</div>
            <h1 className="m-0 display-title text-4xl leading-none text-text-primary">DFS Dashboard</h1>
            <p className="m-0 mt-2 text-sm text-text-secondary">
              {gamesDate
                ? new Date(gamesDate + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })
                : 'Today'}
              {gamesSource === 'theodds' && <span className="ml-2 text-[11px] text-success">● Live Odds</span>}
              {(gamesSource === 'mock' || gamesSource === 'slate-mock') && <span className="ml-2 text-[11px] text-warning">● Demo Odds</span>}
            </p>
          </div>
          <button
            onClick={() => router.push('/optimizer')}
            className="px-5 py-2.5 bg-primary text-[#101722] border border-primary rounded-full text-[13px] font-black cursor-pointer flex items-center gap-2 shadow-[0_0_24px_rgba(244,181,64,0.22)]"
          >
            Go to Optimizer
            {excludedCount > 0 && (
              <span className="bg-danger rounded-full px-2 py-px text-[11px] font-bold">
                {excludedCount} faded
              </span>
            )}
          </button>
        </div>

        {/* ========== Odds Strip ========== */}
        <section className="mb-[30px]">
          <div className="flex items-center justify-between mb-2.5">
            <h2 className="m-0 section-label">
              Today&apos;s Games
            </h2>
            <span className="text-[11px] text-text-muted">{games.length} game{games.length !== 1 ? 's' : ''}</span>
          </div>

          {gamesLoading ? (
            <div className="flex gap-2.5">
              {[1,2,3,4].map(i => (
                <div key={i} className="shrink-0 w-52 h-28 glass-panel rounded-2xl opacity-40" />
              ))}
            </div>
          ) : (
            <div className="flex gap-2.5 overflow-x-auto pb-1.5 scrollbar-thin">
              {games.length === 0
                ? <div className="text-[#475569] text-[13px] py-5">No games scheduled for today.</div>
                : games.map(g => <GameCard key={g.id} game={g} />)
              }
            </div>
          )}
        </section>

        {/* ========== Team Filter Panel ========== */}
        <section className="mb-[30px]">
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <div>
              <h2 className="m-0 section-label">
                Slate Teams
              </h2>
              {latestSlate && (
                <p className="m-0 mt-[3px] text-[11px] text-text-muted">
                  {latestSlate.platform.toUpperCase()} · {latestSlate.sport.toUpperCase()} · {latestSlate.date}
                  {teams.length > 0 ? ` · ${teams.length} teams` : ''}
                </p>
              )}
            </div>
            {teams.length > 0 && (
              <div className="flex gap-2">
                <button onClick={includeAll} className={btnBlueCls}>Include All</button>
                <button onClick={fadeAll}    className={btnRedCls}>Fade All</button>
              </div>
            )}
          </div>

          {slatesLoading ? (
            <div className="flex flex-wrap gap-2.5">
              {[...Array(8)].map((_, i) => (
                <div key={i} className="w-20 h-[68px] bg-[#111827] rounded-lg opacity-40" />
              ))}
            </div>
          ) : teams.length === 0 ? (
            <div className="glass-panel border-dashed rounded-[20px] px-6 py-8 text-center text-text-muted">
              <div className="text-2xl mb-2">📋</div>
              <div className="text-sm font-semibold text-text-secondary mb-1">No slate uploaded yet</div>
              <div className="text-xs">
                Upload a CSV on the{' '}
                <span onClick={() => router.push('/slates')} className="text-[#7dd3fc] cursor-pointer underline">
                  Slates page
                </span>{' '}
                to see team controls here.
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2.5">
              {teams.map(abbr => {
                const included = !excludedTeams.has(abbr)
                return (
                  <button
                    key={abbr}
                    onClick={() => toggleTeam(abbr)}
                    title={included ? 'Click to fade this team' : 'Click to include this team'}
                    style={{'--tc': teamColor(abbr)} as React.CSSProperties}
                    className={cn(
                      'w-20 h-[68px] rounded-2xl flex flex-col items-center justify-center gap-1 relative cursor-pointer',
                      'transition-opacity duration-150',
                      included
                        ? 'border-2 border-[var(--tc)] bg-[color-mix(in_srgb,var(--tc)_14%,rgba(12,20,34,0.85))] opacity-100'
                        : 'border-2 border-surface-border/50 bg-[rgba(7,14,24,0.72)] opacity-40'
                    )}
                  >
                    <div className={cn('w-8 h-8 rounded-[6px] flex items-center justify-center text-[10px] font-extrabold text-white', included ? 'bg-[var(--tc)]' : 'bg-[#1e2a3a]')}>
                      {abbr.slice(0, 3)}
                    </div>
                    <span className={`text-[10px] font-semibold ${included ? 'text-text-primary' : 'text-text-muted'}`}>
                      {abbr}
                    </span>
                    {!included && (
                      <span className="absolute top-[3px] right-[5px] text-[9px] text-danger font-bold">
                        OUT
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          )}

          {excludedCount > 0 && teams.length > 0 && (
            <div className="mt-3.5 px-3.5 py-2.5 bg-[#2b1018] border border-[#5b1726] rounded-2xl text-xs text-[#fecdd3] flex items-center justify-between flex-wrap gap-2">
              <span>
                <strong>{excludedCount}</strong> team{excludedCount !== 1 ? 's' : ''} faded —
                players from these teams will be excluded when you run the optimizer.
              </span>
              <button onClick={includeAll} className={btnDangerCls}>
                Clear fades
              </button>
            </div>
          )}
        </section>

        {/* ========== Player Pool ========== */}
        {(players.length > 0 || playersLoading) && (
          <section className="mb-7">
            {/* Toolbar */}
            <div
              className="flex items-center gap-2 mb-2.5 flex-wrap"
              onClick={() => showColMenu && setShowColMenu(false)}
            >
              <h2 className="m-0 mr-1 section-label">
                Players
              </h2>

              {/* Column toggle */}
              <div className="relative">
                <button
                  onClick={e => { e.stopPropagation(); setShowColMenu(v => !v) }}
                  className={btnGrayCls + ' flex items-center gap-1.5'}
                >
                  ⊞ Columns
                </button>
                {showColMenu && (
                  <div
                    onClick={e => e.stopPropagation()}
                    className="absolute top-[110%] left-0 z-50 glass-panel rounded-2xl py-2 min-w-[140px] shadow-[0_8px_24px_rgba(0,0,0,0.5)]"
                  >
                    {ALL_COLS.map(col => (
                      <label key={col.key} className="flex items-center gap-2 px-3.5 py-1.5 cursor-pointer text-xs text-text-primary select-none">
                        <input
                          type="checkbox"
                          checked={visibleCols.has(col.key)}
                          onChange={() => toggleCol(col.key)}
                          className="w-[13px] h-[13px] accent-blue-500"
                        />
                        {col.label}
                      </label>
                    ))}
                  </div>
                )}
              </div>

              {/* Position filter tabs */}
              <div className="flex gap-1">
                {POSITIONS.map(pos => (
                  <button key={pos} onClick={() => { setPosFilter(pos); setTablePage(0) }}
                    className={`px-2.5 py-1 rounded-[5px] text-[11px] font-semibold cursor-pointer border-none ${
                      posFilter === pos ? 'bg-[#4d3a13] text-[#f4b540]' : 'bg-[rgba(7,14,24,0.58)] text-text-muted'
                    }`}>
                    {pos}
                  </button>
                ))}
              </div>

              {/* Search */}
              <div className="flex-1 min-w-[180px] max-w-[300px] relative ml-auto">
                <span className="absolute left-[9px] top-1/2 -translate-y-1/2 text-xs text-[#475569] pointer-events-none">🔍</span>
                <input
                  value={search}
                  onChange={e => { setSearch(e.target.value); setTablePage(0) }}
                  placeholder="Search players…"
                  className="w-full glass-strip text-text-primary rounded-full py-2 pr-2.5 pl-7 text-xs outline-none box-border"
                />
                {search && (
                  <button onClick={() => { setSearch(''); setTablePage(0) }} className="absolute right-2 top-1/2 -translate-y-1/2 bg-transparent border-none text-[#475569] cursor-pointer text-sm leading-none">✕</button>
                )}
              </div>

              <span className="text-[11px] text-text-muted whitespace-nowrap">
                {filteredPlayers.length} of {players.length}
              </span>
            </div>

            {/* Table */}
            <div className="glass-panel rounded-[24px] overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr className="bg-[rgba(7,14,24,0.92)] border-b border-surface-border">
                      <th className={thStyle('left')}   onClick={() => handleSort('name')}>PLAYER{sortIcon('name')}</th>
                      <th className={thStyle('left')}>POS</th>
                      <th className={thStyle('left')}>TEAM</th>
                      <th className={thStyle('left')}>OPP</th>
                      {visibleCols.has('salary') && <th className={thStyle('right')}  onClick={() => handleSort('salary')}>SALARY{sortIcon('salary')}</th>}
                      {visibleCols.has('fppg')   && <th className={thStyle('right')}  onClick={() => handleSort('fppg')}>FPPG{sortIcon('fppg')}</th>}
                      {visibleCols.has('value')  && <th className={thStyle('right')}  onClick={() => handleSort('value')}>VALUE{sortIcon('value')}</th>}
                      {visibleCols.has('injury') && <th className={thStyle('center')}>STATUS</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {playersLoading
                      ? [...Array(8)].map((_, i) => (
                          <tr key={i} className="border-b border-[#0d1117]">
                            {[1,2,3,4,5,6,7].map(j => (
                              <td key={j} className="px-3 py-2.5">
                                <div className="h-3 bg-surface-border rounded opacity-50" />
                              </td>
                            ))}
                          </tr>
                        ))
                      : pageRows.length === 0
                        ? <tr><td colSpan={8} className="px-6 py-6 text-center text-[#475569]">No players match the current filters.</td></tr>
                        : pageRows.map((p, i) => {
                            const teamFaded   = excludedTeams.has(p.team)
                            const playerSkipped = excludedPlayers.has(p.name)
                            const faded = teamFaded || playerSkipped
                            return (
                              <tr
                                key={`${p.name}-${i}`}
                                onClick={() => togglePlayer(p.name)}
                                title={playerSkipped ? 'Click to un-skip this player' : 'Click to skip this player'}
                                className={cn('border-b border-surface-border/40 cursor-pointer transition-opacity duration-100', i % 2 === 0 ? 'bg-[rgba(255,255,255,0.01)]' : 'bg-[rgba(7,14,24,0.22)]', faded ? 'opacity-30' : 'opacity-100')}
                              >
                                {/* Player name */}
                                <td className="px-3 py-2 whitespace-nowrap">
                                  <div className="flex items-center gap-1.5">
                                    <span className={`font-semibold ${faded ? 'text-text-muted' : 'text-text-primary'}`}>{p.name}</span>
                                    {teamFaded   && <span className="text-[9px] text-danger font-extrabold bg-[#3b0000] rounded-[3px] px-1 py-px">TEAM OUT</span>}
                                    {playerSkipped && !teamFaded && <span className="text-[9px] text-warning font-extrabold bg-[#451a03] rounded-[3px] px-1 py-px">SKIP</span>}
                                  </div>
                                </td>
                                {/* Position */}
                                <td className="px-3 py-2 whitespace-nowrap">
                                    <span className="text-[10px] text-text-muted bg-[rgba(129,143,166,0.12)] rounded-full px-2 py-0.5 font-semibold border border-surface-border/40">
                                    {p.position_raw || p.position}
                                  </span>
                                </td>
                                {/* Team */}
                                <td className="px-3 py-2">
                                  <div className="flex items-center gap-1.5">
                                    <div className="w-[18px] h-[18px] rounded-[3px] flex items-center justify-center text-[7px] font-extrabold text-white shrink-0 bg-[var(--pt)]" style={{'--pt': teamColor(p.team)} as React.CSSProperties}>
                                      {p.team.slice(0, 3)}
                                    </div>
                                    <span className={`font-semibold text-[11px] ${faded ? 'text-text-muted' : 'text-text-primary'}`}>{p.team}</span>
                                  </div>
                                </td>
                                {/* Opponent */}
                                <td className="px-3 py-2">
                                  <span className="text-text-muted text-[11px]">
                                    {p.opponent ? `vs ${p.opponent}` : '—'}
                                  </span>
                                </td>
                                {/* Salary */}
                                {visibleCols.has('salary') && (
                                  <td className={cn('px-3 py-2 text-right font-mono font-bold whitespace-nowrap', salaryColor(p.salary))}>
                                    ${p.salary.toLocaleString()}
                                  </td>
                                )}
                                {/* FPPG */}
                                {visibleCols.has('fppg') && (
                                  <td className={cn('px-3 py-2 text-right font-mono', projColor(p.fppg))}>
                                    {p.fppg > 0 ? p.fppg.toFixed(2) : '—'}
                                  </td>
                                )}
                                {/* Value */}
                                {visibleCols.has('value') && (
                                  <td className={cn('px-3 py-2 text-right font-mono', valColor(p.value))}>
                                    {p.value > 0 ? p.value.toFixed(2) : '—'}
                                  </td>
                                )}
                                {/* Injury */}
                                {visibleCols.has('injury') && (
                                  <td className="px-3 py-2 text-center">
                                    {p.injury
                                      ? <span className="text-[10px] font-bold text-warning bg-[#4a3514] rounded-full px-2 py-0.5 border border-[#fbbf24]/15">{p.injury}</span>
                                      : <span className="text-[10px] text-success">—</span>
                                    }
                                  </td>
                                )}
                              </tr>
                            )
                          })
                    }
                  </tbody>
                </table>
              </div>

              {/* Pagination footer */}
              {filteredPlayers.length > 0 && (
                <div className="flex items-center justify-between px-4 py-3 border-t border-surface-border flex-wrap gap-2 bg-[rgba(7,14,24,0.35)]">
                  <div className="flex items-center gap-1.5 text-[11px] text-text-muted">
                    <span>Rows:</span>
                    {[25, 50, 100].map(n => (
                      <button key={n} onClick={() => { setRowsPerPage(n); setTablePage(0) }}
                        className={`px-2 py-[3px] border-none rounded cursor-pointer text-[11px] font-semibold ${
                          rowsPerPage === n ? 'bg-[#4d3a13] text-[#f4b540]' : 'bg-[rgba(7,14,24,0.58)] text-text-muted'
                        }`}>
                        {n}
                      </button>
                    ))}
                  </div>
                  <div className="flex items-center gap-2 text-[11px] text-text-muted">
                    <span>
                      {tablePage * rowsPerPage + 1}–{Math.min((tablePage + 1) * rowsPerPage, filteredPlayers.length)} of {filteredPlayers.length}
                    </span>
                    <button onClick={() => setTablePage(p => Math.max(0, p - 1))} disabled={tablePage === 0}
                      className={`px-[9px] py-[3px] bg-[rgba(7,14,24,0.58)] border-none rounded-[8px] text-[13px] ${
                        tablePage === 0 ? 'text-slate-700 cursor-default' : 'text-text-secondary cursor-pointer'
                      }`}>‹</button>
                    <button onClick={() => setTablePage(p => Math.min(totalPages - 1, p + 1))} disabled={tablePage >= totalPages - 1}
                      className={`px-[9px] py-[3px] bg-[rgba(7,14,24,0.58)] border-none rounded-[8px] text-[13px] ${
                        tablePage >= totalPages - 1 ? 'text-slate-700 cursor-default' : 'text-text-secondary cursor-pointer'
                      }`}>›</button>
                  </div>
                </div>
              )}
            </div>
          </section>
        )}

        {/* ========== Quick Links ========== */}
        <div className="flex gap-2.5 flex-wrap mt-1">
          {[
            { href: '/slates',      icon: '📂', label: 'Upload Slate',  desc: 'Import your DFS contest file' },
            { href: '/projections', icon: '📈', label: 'Projections',   desc: 'Generate player projections'  },
            { href: '/optimizer',   icon: '⚡', label: 'Optimizer',     desc: 'Build optimal lineups'        },
            { href: '/simulation',  icon: '🎲', label: 'Simulation',    desc: 'Monte Carlo lineup testing'   },
          ].map(({ href, icon, label, desc }) => (
            <div
              key={href}
              onClick={() => router.push(href)}
              className="flex-[1_1_180px] min-w-[160px] glass-panel rounded-[20px] px-4 py-4 cursor-pointer hover:border-[#f4b540]/22 transition-colors"
            >
              <div className="text-xl">{icon}</div>
              <div className="text-[13px] font-semibold text-text-primary mt-1.5">{label}</div>
              <div className="text-[11px] text-text-muted mt-0.5">{desc}</div>
            </div>
          ))}
        </div>

      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Table cell color helpers
// ---------------------------------------------------------------------------

function salaryColor(sal: number): string {
  if (sal >= 9000) return 'text-pink-400'
  if (sal >= 7500) return 'text-orange-400'
  if (sal >= 6000) return 'text-yellow-400'
  return 'text-slate-400'
}

function projColor(pts: number): string {
  if (pts >= 45) return 'text-emerald-400'
  if (pts >= 35) return 'text-green-300'
  if (pts >= 25) return 'text-slate-100'
  return 'text-slate-400'
}

function valColor(v: number): string {
  if (v >= 6) return 'text-emerald-400'
  if (v >= 5) return 'text-green-300'
  if (v >= 4) return 'text-slate-100'
  return 'text-slate-400'
}

function thStyle(align: 'left' | 'right' | 'center'): string {
  return `px-3 py-[9px] text-${align} text-[10px] font-black text-text-muted uppercase tracking-[0.14em] cursor-pointer whitespace-nowrap select-none`
}
