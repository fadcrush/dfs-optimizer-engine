'use client'

/**
 * PlayerPoolPanel
 * ===============
 * Full player-pool management: per-player include/exclude, inline projection /
 * ownership editing, bulk actions, filter-by-status, position filter, and search.
 *
 * Architecture notes
 * ------------------
 * • Projection edits, ownership edits, and pool inclusion are three separate,
 *   independent layers of state stored inside each PlayerPoolEntry.
 * • The optimizer receives:
 *     out_players       → names of excluded entries
 *     projection_overrides → entries where adjProj !== stockProj
 *     locked_players    → names with poolStatus === 'locked' (future)
 * • Filtering / sorting is purely derived — source data is never mutated.
 * • When the slate changes (new file loaded), the parent resets the poolMap.
 *   Existing edits do NOT bleed across slates.
 */

import { useState, useMemo, useRef } from 'react'
import { FixedSizeList } from 'react-window'
import { cn } from '@/lib/utils'

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export type PoolStatus = 'included' | 'excluded'

export interface PlayerPoolEntry {
  /** Stable key: `${name}::${team}` */
  id: string
  name: string
  pos: string
  team: string
  salary: number
  /** Projection from the uploaded CSV (never mutated after parse) */
  stockProj: number
  /** User-adjusted projection — fed to backend as projection_overrides */
  adjProj: number
  /** Ownership from the uploaded CSV (never mutated after parse) */
  stockOwn: number
  /** User-adjusted ownership (informational; not sent to backend currently) */
  adjOwn: number
  poolStatus: PoolStatus
}

export type PlayerPoolMap = Map<string, PlayerPoolEntry>

type SortKey = 'name' | 'pos' | 'team' | 'salary' | 'adjProj' | 'adjOwn'
type ViewFilter = 'all' | 'included' | 'excluded'

// ─────────────────────────────────────────────────────────────────────────────
// CSV parser (shared utility — also used externally by the optimizer page)
// ─────────────────────────────────────────────────────────────────────────────

function _parseCSVLine(line: string): string[] {
  const result: string[] = []
  let current = ''
  let inQuotes = false
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]
    if (ch === '"') { inQuotes = !inQuotes }
    else if (ch === ',' && !inQuotes) { result.push(current); current = '' }
    else { current += ch }
  }
  result.push(current)
  return result
}

function _col(headers: string[], ...aliases: string[]): number {
  const aliasSet = new Set(aliases)
  return headers.findIndex(h => aliasSet.has(h.toLowerCase().trim()))
}

/**
 * Parse an uploaded DK or FD CSV into a PlayerPoolMap keyed by `name::team`.
 * Safe to call on any slate format — returns empty map on malformed input.
 */
export async function parseCSVToPool(file: File): Promise<PlayerPoolMap> {
  let text = ''
  try { text = await file.text() } catch { return new Map() }

  const rawLines = text.split(/\r?\n/)
  const dataLines = rawLines.filter(l => l.trim())
  if (dataLines.length < 2) return new Map()

  const rawHeaders = _parseCSVLine(dataLines[0])
  const headers = rawHeaders.map(h => h.toLowerCase().trim())

  const nameIdx   = _col(headers, 'name', 'nickname', 'player', 'playername', 'player_name')
  const posIdx    = _col(headers, 'pos', 'position', 'roster position')
  const teamIdx   = _col(headers, 'team', 'teamabbrev', 'team_abbrev')
  const salIdx    = _col(headers, 'salary', 'sal')
  const projIdx   = _col(headers, 'proj', 'projection', 'my proj', 'ss proj')
  // FD uses FPPG as their stock average; DK uses AvgPointsPerGame — both shown
  // as stockProj for display only (backend will replace with its own projections)
  const fppgIdx   = projIdx === -1 ? _col(headers, 'fppg', 'avgpointspergame', 'points', 'avg points per game') : -1
  const ownIdx    = _col(headers, 'own', 'own%', 'ownership', '%owned', 'own_est', 'ownership%')

  if (nameIdx === -1) return new Map()

  const map: PlayerPoolMap = new Map()

  for (let i = 1; i < dataLines.length; i++) {
    const cols = _parseCSVLine(dataLines[i])
    let rawName = cols[nameIdx]?.trim() ?? ''
    if (!rawName) continue
    // Strip DK "123456789:Name" prefix
    if (/^\d+:/.test(rawName)) rawName = rawName.split(':', 2)[1].trim()
    if (!rawName) continue

    const pos    = posIdx  >= 0 ? cols[posIdx]?.trim()  ?? '' : ''
    const team   = teamIdx >= 0 ? cols[teamIdx]?.trim() ?? '' : ''
    const salary = salIdx  >= 0 ? parseFloat((cols[salIdx] ?? '').replace(/[^0-9.]/g, '')) || 0 : 0

    let stockProj = 0
    if (projIdx >= 0 && cols[projIdx]?.trim()) {
      stockProj = parseFloat(cols[projIdx]) || 0
    } else if (fppgIdx >= 0 && cols[fppgIdx]?.trim()) {
      stockProj = parseFloat(cols[fppgIdx]) || 0
    }

    const stockOwn = ownIdx >= 0 ? parseFloat(cols[ownIdx] ?? '0') || 0 : 0

    const id = `${rawName}::${team}`
    // Deduplicate — first occurrence wins (matches backend behaviour)
    if (!map.has(id)) {
      map.set(id, {
        id,
        name: rawName,
        pos,
        team,
        salary,
        stockProj,
        adjProj: stockProj,
        stockOwn,
        adjOwn: stockOwn,
        poolStatus: 'included',
      })
    }
  }

  return map
}

// ─────────────────────────────────────────────────────────────────────────────
// Derive optimizer payload helpers (exported for use in page.tsx run handlers)
// ─────────────────────────────────────────────────────────────────────────────

/** Names of all excluded players — passed as `out_players` to backend */
export function getPoolExcludedNames(poolMap: PlayerPoolMap): string[] {
  const names: string[] = []
  poolMap.forEach(e => { if (e.poolStatus === 'excluded') names.push(e.name) })
  return names
}

/** Only entries where adjProj differs from stockProj — passed as `projection_overrides` */
export function getPoolProjectionOverrides(poolMap: PlayerPoolMap): Record<string, number> {
  const overrides: Record<string, number> = {}
  poolMap.forEach(e => {
    if (Math.abs(e.adjProj - e.stockProj) > 0.001) {
      overrides[e.name] = e.adjProj
    }
  })
  return overrides
}

/** Validation: check if the included pool is viable for lineup construction */
export interface PoolValidationResult {
  ok: boolean
  warnings: string[]
  errors: string[]
  includedCount: number
  totalCount: number
  byPosition: Record<string, number>
}

export function validatePool(poolMap: PlayerPoolMap, site: 'FD' | 'DK'): PoolValidationResult {
  const MIN_POOL = site === 'FD' ? 14 : 14
  const warnings: string[] = []
  const errors: string[] = []
  const byPosition: Record<string, number> = {}
  let includedCount = 0
  const totalCount = poolMap.size

  poolMap.forEach(e => {
    if (e.poolStatus === 'included') {
      includedCount++
      // Split multi-position eligibility strings (e.g. "SG/SF", "PG/SG") so
      // each component position gets counted independently.
      e.pos.split('/').map(p => p.trim()).filter(Boolean).forEach(p => {
        byPosition[p] = (byPosition[p] ?? 0) + 1
      })
    }
  })

  if (includedCount === 0) {
    errors.push('No players are included in the pool — cannot build any lineups.')
  } else if (includedCount < MIN_POOL) {
    errors.push(`Only ${includedCount} players included — need at least ${MIN_POOL} to build viable lineups.`)
  } else if (includedCount < MIN_POOL + 10) {
    warnings.push(`Pool has only ${includedCount} players — diversity will be limited.`)
  }

  // Hard minimums: the absolute floor to fill ONE valid lineup
  const fdHard = { PG: 2, SG: 2, SF: 2, PF: 2, C: 1 }
  const dkHard = { PG: 1, SG: 1, SF: 1, PF: 1, C: 1 }
  // Soft minimums: recommended for meaningful lineup variety across a full entry set
  const fdSoft = { PG: 4, SG: 4, SF: 4, PF: 4, C: 2 }
  const dkSoft = { PG: 3, SG: 3, SF: 3, PF: 3, C: 2 }
  const hard = site === 'FD' ? fdHard : dkHard
  const soft = site === 'FD' ? fdSoft : dkSoft

  Object.entries(hard).forEach(([pos, min]) => {
    const count = byPosition[pos] ?? 0
    if (count < min) {
      errors.push(`Position ${pos}: only ${count} included — need at least ${min} to fill a lineup.`)
    }
  })
  // Only show variety warnings when there are no hard errors
  if (errors.length === 0) {
    Object.entries(soft).forEach(([pos, min]) => {
      const count = byPosition[pos] ?? 0
      if (count < min) {
        warnings.push(`Position ${pos}: only ${count} included (≥ ${min} recommended for lineup variety).`)
      }
    })
  }

  return { ok: errors.length === 0, warnings, errors, includedCount, totalCount, byPosition }
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

interface PlayerPoolPanelProps {
  poolMap: PlayerPoolMap
  onPoolMapChange: (map: PlayerPoolMap) => void
  site?: 'FD' | 'DK'
  /** Collapsed by default until the user chooses to open the panel */
  defaultOpen?: boolean
}

export function PlayerPoolPanel({
  poolMap,
  onPoolMapChange,
  site = 'FD',
  defaultOpen = false,
}: PlayerPoolPanelProps) {
  // ── panel collapsed state ──────────────────────────────────────────────────
  const [open, setOpen] = useState(defaultOpen)

  // ── view controls (local UI state only — never affects optimizer payload) ──
  const [viewFilter, setViewFilter] = useState<ViewFilter>('all')
  const [search, setSearch] = useState('')
  const [posFilter, setPosFilter] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('adjProj')
  const [sortDesc, setSortDesc] = useState(true)

  // ── inline-edit state ──────────────────────────────────────────────────────
  // editingCell = `${id}:proj` | `${id}:own` | null
  const [editingCell, setEditingCell] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const editInputRef = useRef<HTMLInputElement>(null)

  // ─────────────────────────────────────────────────────────────────────────
  // Derived data
  // ─────────────────────────────────────────────────────────────────────────

  const allEntries = useMemo(() => Array.from(poolMap.values()), [poolMap])
  const totalCount    = allEntries.length
  const includedCount = allEntries.filter(e => e.poolStatus === 'included').length
  const excludedCount = allEntries.filter(e => e.poolStatus === 'excluded').length

  const allPositions = useMemo(() => {
    const pos = new Set(allEntries.map(e => e.pos).filter(Boolean))
    return Array.from(pos).sort()
  }, [allEntries])

  const hasAdjustedProj = useMemo(
    () => allEntries.some(e => Math.abs(e.adjProj - e.stockProj) > 0.001),
    [allEntries],
  )

  const visibleEntries = useMemo(() => {
    let rows = allEntries
    if (viewFilter === 'included') rows = rows.filter(e => e.poolStatus === 'included')
    if (viewFilter === 'excluded') rows = rows.filter(e => e.poolStatus === 'excluded')
    if (posFilter) rows = rows.filter(e => e.pos === posFilter)
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      rows = rows.filter(e => e.name.toLowerCase().includes(q) || e.team.toLowerCase().includes(q))
    }
    return [...rows].sort((a, b) => {
      let av = 0, bv = 0
      switch (sortKey) {
        case 'salary':  av = a.salary;  bv = b.salary;  break
        case 'adjProj': av = a.adjProj; bv = b.adjProj; break
        case 'adjOwn':  av = a.adjOwn;  bv = b.adjOwn;  break
        case 'name': return sortDesc ? b.name.localeCompare(a.name) : a.name.localeCompare(b.name)
        case 'pos':  return sortDesc ? b.pos.localeCompare(a.pos)   : a.pos.localeCompare(b.pos)
        case 'team': return sortDesc ? b.team.localeCompare(a.team) : a.team.localeCompare(b.team)
      }
      return sortDesc ? bv - av : av - bv
    })
  }, [allEntries, viewFilter, posFilter, search, sortKey, sortDesc])

  const allVisibleIncluded = visibleEntries.length > 0 && visibleEntries.every(e => e.poolStatus === 'included')
  const someVisibleIncluded = visibleEntries.some(e => e.poolStatus === 'included')

  const validation = useMemo(() => validatePool(poolMap, site), [poolMap, site])

  // ─────────────────────────────────────────────────────────────────────────
  // Mutations
  // ─────────────────────────────────────────────────────────────────────────

  const update = (patches: Array<[string, Partial<PlayerPoolEntry>]>) => {
    const next = new Map(poolMap)
    patches.forEach(([id, patch]) => {
      const e = next.get(id)
      if (e) next.set(id, { ...e, ...patch })
    })
    onPoolMapChange(next)
  }

  const togglePlayer = (id: string) => {
    const e = poolMap.get(id)
    if (!e) return
    const next = new Map(poolMap)
    next.set(id, { ...e, poolStatus: e.poolStatus === 'included' ? 'excluded' : 'included' })
    onPoolMapChange(next)
  }

  const includeAll = () => {
    const next = new Map(poolMap)
    allEntries.forEach(e => next.set(e.id, { ...e, poolStatus: 'included' }))
    onPoolMapChange(next)
  }

  const excludeAll = () => {
    const next = new Map(poolMap)
    allEntries.forEach(e => next.set(e.id, { ...e, poolStatus: 'excluded' }))
    onPoolMapChange(next)
  }

  const resetPool = () => {
    const next = new Map(poolMap)
    allEntries.forEach(e =>
      next.set(e.id, { ...e, poolStatus: 'included', adjProj: e.stockProj, adjOwn: e.stockOwn })
    )
    onPoolMapChange(next)
  }

  const toggleBulkVisible = () => {
    const newStatus: PoolStatus = allVisibleIncluded ? 'excluded' : 'included'
    update(visibleEntries.map(e => [e.id, { poolStatus: newStatus }]))
  }

  // ── Top-N-per-team preset ────────────────────────────────────────────────
  const [topN, setTopN] = useState(7)

  const applyTopNPerTeam = (n: number) => {
    // Group all entries by team, sort each group by adjProj desc, keep top-n included
    const byTeam = new Map<string, PlayerPoolEntry[]>()
    allEntries.forEach(e => {
      const key = e.team || '__unknown__'
      if (!byTeam.has(key)) byTeam.set(key, [])
      byTeam.get(key)!.push(e)
    })
    const patches: Array<[string, Partial<PlayerPoolEntry>]> = []
    byTeam.forEach(players => {
      const sorted = [...players].sort((a, b) => (b.adjProj ?? 0) - (a.adjProj ?? 0))
      sorted.forEach((e, idx) => {
        patches.push([e.id, { poolStatus: idx < n ? 'included' : 'excluded' }])
      })
    })
    update(patches)
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Inline editing
  // ─────────────────────────────────────────────────────────────────────────

  const startEdit = (id: string, field: 'proj' | 'own', currentVal: number) => {
    setEditingCell(`${id}:${field}`)
    setEditValue(String(currentVal))
    setTimeout(() => editInputRef.current?.select(), 0)
  }

  const commitEdit = () => {
    if (!editingCell) return
    const [id, field] = editingCell.split(':') as [string, 'proj' | 'own']
    const num = parseFloat(editValue)
    if (!isNaN(num) && num >= 0) {
      update([[id, field === 'proj' ? { adjProj: num } : { adjOwn: num }]])
    }
    setEditingCell(null)
    setEditValue('')
  }

  const cancelEdit = () => {
    setEditingCell(null)
    setEditValue('')
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Sort toggle
  // ─────────────────────────────────────────────────────────────────────────

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDesc(!sortDesc)
    else { setSortKey(key); setSortDesc(true) }
  }

  const sortIndicator = (key: SortKey) =>
    sortKey === key ? (sortDesc ? ' ▼' : ' ▲') : ''

  // ─────────────────────────────────────────────────────────────────────────
  // Virtual row renderer (defined inline to share closure state)
  // ─────────────────────────────────────────────────────────────────────────

  const ROW_HEIGHT = 38

  function VirtualRow({
    index,
    style,
    data,
  }: {
    index: number
    style: React.CSSProperties
    data: PlayerPoolEntry[]
  }) {
    const entry = data[index]
    const excluded = entry.poolStatus === 'excluded'
    const projCellKey = `${entry.id}:proj`
    const ownCellKey  = `${entry.id}:own`
    const editingProj = editingCell === projCellKey
    const editingOwn  = editingCell === ownCellKey
    const projModified = Math.abs(entry.adjProj - entry.stockProj) > 0.001
    const ownModified  = Math.abs(entry.adjOwn  - entry.stockOwn)  > 0.001

    return (
      <div
        style={style}
        className={cn(
          'flex items-center border-b border-surface-border/40 text-xs select-none',
          excluded ? 'opacity-50' : 'hover:bg-surface-overlay',
          'transition-colors duration-75',
        )}
      >
        {/* Checkbox */}
        <div className="w-9 flex-shrink-0 flex items-center justify-center px-2">
          <input
            type="checkbox"
            checked={entry.poolStatus === 'included'}
            onChange={() => togglePlayer(entry.id)}
            className="cursor-pointer accent-primary"
            aria-label={`${entry.poolStatus === 'included' ? 'Exclude' : 'Include'} ${entry.name}`}
          />
        </div>
        {/* Name */}
        <div
          className={cn(
            'flex-1 min-w-0 px-2 font-semibold truncate',
            excluded ? 'line-through text-text-muted' : 'text-text-primary',
          )}
          title={entry.name}
        >
          {entry.name}
        </div>
        {/* Pos */}
        <div className="w-12 flex-shrink-0 px-2 text-text-secondary">{entry.pos}</div>
        {/* Team */}
        <div className="w-14 flex-shrink-0 px-2 text-text-secondary">{entry.team}</div>
        {/* Salary */}
        <div className="w-20 flex-shrink-0 px-2 text-right text-text-secondary font-mono">
          ${(entry.salary ?? 0).toLocaleString()}
        </div>
        {/* Proj — ghost input */}
        <div
          className="w-16 flex-shrink-0 px-2 text-right cursor-text"
          title="Click to adjust projection"
          onClick={() => !editingProj && startEdit(entry.id, 'proj', entry.adjProj)}
        >
          {editingProj ? (
            <input
              autoFocus
              type="number"
              step="0.1"
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={commitEdit}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitEdit()
                if (e.key === 'Escape') cancelEdit()
              }}
              className="w-14 bg-primary-muted text-primary border border-primary rounded px-1 text-right outline-none text-xs"
            />
          ) : (
            <span className={cn('font-mono', projModified ? 'text-primary font-bold' : 'text-text-secondary')}>
              {entry.adjProj.toFixed(1)}
              {projModified && <span className="text-[9px] ml-0.5 opacity-70">✎</span>}
            </span>
          )}
        </div>
        {/* Own% — ghost input */}
        <div
          className="w-16 flex-shrink-0 px-2 text-right cursor-text"
          title="Click to adjust ownership"
          onClick={() => !editingOwn && startEdit(entry.id, 'own', entry.adjOwn)}
        >
          {editingOwn ? (
            <input
              autoFocus
              type="number"
              step="0.1"
              min="0"
              max="100"
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={commitEdit}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitEdit()
                if (e.key === 'Escape') cancelEdit()
              }}
              className="w-12 bg-success-muted text-success border border-success/60 rounded px-1 text-right outline-none text-xs"
            />
          ) : (
            <span className={cn('font-mono', ownModified ? 'text-success font-bold' : 'text-text-muted')}>
              {entry.adjOwn > 0 ? `${entry.adjOwn.toFixed(1)}%` : '—'}
              {ownModified && <span className="text-[9px] ml-0.5 opacity-70">✎</span>}
            </span>
          )}
        </div>
        {/* Status badge */}
        <div className="w-20 flex-shrink-0 px-2 flex items-center justify-center">
          {excluded ? (
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-danger-muted text-danger border border-danger/30">
              OUT
            </span>
          ) : (
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-success-muted text-success border border-success/30">
              IN
            </span>
          )}
        </div>
      </div>
    )
  }

  // ─────────────────────────────────────────────────────────────────────────

  if (totalCount === 0) return null

  return (
    <div className="border border-surface-border rounded-xl overflow-hidden mb-5 bg-surface-raised">
      {/* ── Header / toggle ─────────────────────────────────────────────── */}
      <div
        className="flex items-center justify-between px-4 py-3 cursor-pointer select-none hover:bg-surface-overlay transition-colors border-b border-surface-border"
        onClick={() => setOpen((o) => !o)}
        role="button"
        aria-expanded={open}
        aria-label="Toggle player pool panel"
      >
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-bold text-text-primary">Player Pool</span>
          <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-surface-base border border-surface-border text-text-secondary">
            {totalCount} total
          </span>
          <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-success-muted border border-success/30 text-success">
            {includedCount} in pool
          </span>
          {excludedCount > 0 && (
            <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-danger-muted border border-danger/30 text-danger">
              {excludedCount} excluded
            </span>
          )}
          {hasAdjustedProj && (
            <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-primary-muted border border-primary/30 text-primary">
              proj overrides active
            </span>
          )}
        </div>
        <span className="text-xs text-text-muted" aria-hidden="true">{open ? '▲' : '▼'}</span>
      </div>

      {/* ── Expanded panel ──────────────────────────────────────────────── */}
      {open && (
        <div className="p-4">

          {/* Validation errors */}
          {validation.errors.length > 0 && (
            <div className="mb-3 p-3 border border-danger/40 bg-danger-muted rounded-lg">
              {validation.errors.map((e, i) => (
                <p key={i} className={cn('text-xs text-danger', i > 0 && 'mt-1')}>⛔ {e}</p>
              ))}
            </div>
          )}
          {validation.warnings.length > 0 && validation.errors.length === 0 && (
            <div className="mb-3 p-3 border border-warning/40 bg-warning-muted rounded-lg">
              {validation.warnings.map((w, i) => (
                <p key={i} className={cn('text-xs text-warning', i > 0 && 'mt-1')}>⚠ {w}</p>
              ))}
            </div>
          )}

          {/* ── Toolbar ─────────────────────────────────────────────────── */}
          <div className="flex gap-2 items-center flex-wrap mb-3">
            <button onClick={includeAll}
              className="px-3 py-1 rounded text-xs font-semibold bg-success-muted text-success border border-success/40 hover:border-success/70 transition-colors cursor-pointer">
              Include All
            </button>
            <button onClick={excludeAll}
              className="px-3 py-1 rounded text-xs font-semibold bg-danger-muted text-danger border border-danger/40 hover:border-danger/70 transition-colors cursor-pointer">
              Exclude All
            </button>
            <button onClick={resetPool}
              className="px-3 py-1 rounded text-xs font-semibold bg-surface-overlay text-text-secondary border border-surface-border hover:text-text-primary transition-colors cursor-pointer">
              Reset Pool
            </button>

            <div className="w-px h-5 bg-surface-border mx-1" aria-hidden="true" />

            {/* Top-N per team */}
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] text-text-secondary whitespace-nowrap">Top</span>
              <input
                type="number"
                min={1}
                max={15}
                value={topN}
                onChange={(e) => setTopN(Math.max(1, Math.min(15, Number(e.target.value) || 1)))}
                className="w-10 bg-surface-base text-text-primary border border-surface-border rounded px-1.5 py-1 text-xs text-center outline-none focus:border-primary"
                aria-label="Top N players per team"
              />
              <span className="text-[11px] text-text-secondary whitespace-nowrap">/team</span>
              <button
                onClick={() => applyTopNPerTeam(topN)}
                title={`Keep only the top ${topN} players by projection per team`}
                className="px-3 py-1 rounded text-xs font-semibold bg-primary-muted text-primary border border-primary/40 hover:border-primary/70 transition-colors cursor-pointer"
              >
                Apply
              </button>
            </div>

            <div className="w-px h-5 bg-surface-border mx-1" aria-hidden="true" />

            {/* View filter */}
            {(['all', 'included', 'excluded'] as ViewFilter[]).map((f) => (
              <button
                key={f}
                onClick={() => setViewFilter(f)}
                className={cn(
                  'px-3 py-1 rounded text-xs font-semibold border transition-colors cursor-pointer capitalize',
                  viewFilter === f
                    ? 'bg-primary text-white border-primary'
                    : 'bg-surface-overlay text-text-secondary border-surface-border hover:text-text-primary',
                )}
              >
                {f}
              </button>
            ))}

            <div className="w-px h-5 bg-surface-border mx-1" aria-hidden="true" />

            {/* Search */}
            <input
              type="text"
              placeholder="Search name or team…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="bg-surface-base text-text-primary border border-surface-border rounded px-2.5 py-1 text-xs outline-none focus:border-primary w-44 placeholder:text-text-muted"
              aria-label="Search players"
            />

            {/* Position filter */}
            {allPositions.length > 0 && (
              <select
                value={posFilter}
                onChange={(e) => setPosFilter(e.target.value)}
                className="bg-surface-base text-text-primary border border-surface-border rounded px-2.5 py-1 text-xs outline-none focus:border-primary cursor-pointer"
                aria-label="Filter by position"
              >
                <option value="">All Positions</option>
                {allPositions.map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            )}

            <span className="text-[11px] text-text-muted ml-auto whitespace-nowrap">
              Showing {visibleEntries.length} of {totalCount}
            </span>
          </div>

          {/* ── Virtualized table ───────────────────────────────────────── */}
          {visibleEntries.length === 0 ? (
            <div className="py-8 text-center text-sm text-text-muted bg-surface-base rounded-lg border border-dashed border-surface-border">
              No players match the current filters.
            </div>
          ) : (
            <div className="border border-surface-border rounded-lg overflow-hidden">
              {/* Column header — must match VirtualRow column widths exactly */}
              <div className="flex items-center bg-surface-base border-b-2 border-surface-border text-[11px] font-bold uppercase tracking-wide text-text-muted select-none">
                <div className="w-9 flex-shrink-0 flex items-center justify-center px-2 py-2">
                  <input
                    type="checkbox"
                    checked={allVisibleIncluded}
                    ref={(el) => {
                      if (el) el.indeterminate = !allVisibleIncluded && someVisibleIncluded
                    }}
                    onChange={toggleBulkVisible}
                    title="Toggle all visible rows"
                    className="cursor-pointer accent-primary"
                    aria-label="Toggle all visible players"
                  />
                </div>
                <div
                  className="flex-1 min-w-0 px-2 py-2 cursor-pointer hover:text-text-primary transition-colors"
                  onClick={() => toggleSort('name')}
                >
                  Name{sortIndicator('name')}
                </div>
                <div
                  className="w-12 flex-shrink-0 px-2 py-2 cursor-pointer hover:text-text-primary transition-colors"
                  onClick={() => toggleSort('pos')}
                >
                  Pos{sortIndicator('pos')}
                </div>
                <div
                  className="w-14 flex-shrink-0 px-2 py-2 cursor-pointer hover:text-text-primary transition-colors"
                  onClick={() => toggleSort('team')}
                >
                  Team{sortIndicator('team')}
                </div>
                <div
                  className="w-20 flex-shrink-0 px-2 py-2 text-right cursor-pointer hover:text-text-primary transition-colors"
                  onClick={() => toggleSort('salary')}
                >
                  Salary{sortIndicator('salary')}
                </div>
                <div
                  className="w-16 flex-shrink-0 px-2 py-2 text-right text-primary cursor-pointer hover:text-primary/70 transition-colors"
                  onClick={() => toggleSort('adjProj')}
                >
                  Proj{sortIndicator('adjProj')}
                </div>
                <div
                  className="w-16 flex-shrink-0 px-2 py-2 text-right cursor-pointer hover:text-text-primary transition-colors"
                  onClick={() => toggleSort('adjOwn')}
                >
                  Own%{sortIndicator('adjOwn')}
                </div>
                <div className="w-20 flex-shrink-0 px-2 py-2 text-center">Status</div>
              </div>

              {/* Virtualized rows */}
              <FixedSizeList
                height={Math.min(visibleEntries.length * ROW_HEIGHT, 560)}
                itemCount={visibleEntries.length}
                itemSize={ROW_HEIGHT}
                itemData={visibleEntries}
                width="100%"
                className="scrollbar-thin"
              >
                {VirtualRow}
              </FixedSizeList>
            </div>
          )}

          {/* ── Position breakdown ──────────────────────────────────────── */}
          {totalCount > 0 && (
            <div className="mt-3 flex gap-1.5 flex-wrap items-center">
              {Object.entries(validation.byPosition)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([pos, count]) => (
                  <span
                    key={pos}
                    className="px-2 py-0.5 rounded text-[11px] font-semibold bg-surface-base border border-surface-border text-text-secondary"
                  >
                    {pos}: <strong className="text-text-primary">{count}</strong>
                  </span>
                ))}
              <span className="text-[11px] text-text-muted">included by position</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

