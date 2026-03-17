'use client'

import { useState, useMemo } from 'react'
import { formatSalary, cn } from '@/lib/utils'
import type { SwapResult } from '@/lib/api'
import {
  ExclusionReason,
  type CandidatePoolItem,
  type CandidatePoolFilters,
  type CandidatePoolSortKey,
} from '@/lib/late-swap/types'
import {
  buildCandidatePool,
  applyPoolFilters,
  sortPoolItems,
  computePoolSummary,
  type CandidatePoolContext,
} from '@/lib/late-swap/candidatePool'
import { getStatusBadge, getCandidateDiagnostic } from '@/lib/late-swap/candidateDiagnostics'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface Props {
  result: {
    swaps: SwapResult[]
    current_salary: number
    salary_cap: number
  }
  lineupLabel: string
  lineupPlayers: string[]
  scratchedNamesSet: Set<string>
  lockedNamesSet: Set<string>
  poolExcludedPlayers: Set<string>
  site: 'DK' | 'FD'
  /** Fire existing swap flow for a chosen candidate */
  onUseCandidate: (scratchedPlayer: string, candidateName: string, dfsId?: string) => void
  /** Toggle user-exclusion for a player name */
  onToggleExclude: (playerName: string) => void
  /** Allow panel to start collapsed when no result yet */
  defaultOpen?: boolean
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const thCls = 'px-2 py-1.5 text-[10px] font-bold uppercase tracking-[0.05em] text-text-muted border-b-2 border-surface-border text-left whitespace-nowrap bg-surface-base sticky top-0 cursor-pointer select-none'
const tdCls = 'px-2 py-1.5 text-xs border-b border-surface-base whitespace-nowrap'

function SortIcon({ active, dir }: { active: boolean; dir: 'asc' | 'desc' }) {
  if (!active) return <span className="opacity-25 ml-[3px]">↕</span>
  return <span className="ml-[3px] text-[#60a5fa]">{dir === 'desc' ? '↓' : '↑'}</span>
}

const POSITION_OPTIONS = ['PG', 'SG', 'SF', 'PF', 'C', 'G', 'F']

const SORT_OPTIONS: { key: CandidatePoolSortKey; label: string }[] = [
  { key: 'projection', label: 'Projection' },
  { key: 'swapScore',  label: 'Swap Score' },
  { key: 'ceiling',    label: 'Ceiling' },
  { key: 'value',      label: 'Value' },
  { key: 'leverage',   label: 'Leverage' },
  { key: 'ownership',  label: 'Ownership' },
  { key: 'salary',     label: 'Salary' },
]

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function CandidatePoolInspector({
  result,
  lineupLabel,
  lineupPlayers,
  scratchedNamesSet,
  lockedNamesSet,
  poolExcludedPlayers,
  site,
  onUseCandidate,
  onToggleExclude,
  defaultOpen = true,
}: Props) {
  const [open, setOpen] = useState(defaultOpen)
  const [filters, setFilters] = useState<CandidatePoolFilters>({})
  const [sortKey, setSortKey] = useState<CandidatePoolSortKey>('projection')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const allItems = useMemo(() => buildCandidatePool({
    result,
    lineupPlayers,
    scratchedNamesSet,
    lockedNamesSet,
    poolExcludedPlayers,
    site,
  } satisfies CandidatePoolContext), [
    result,
    lineupPlayers,
    scratchedNamesSet,
    lockedNamesSet,
    poolExcludedPlayers,
    site,
  ])

  const filteredAndSorted = useMemo(() => {
    const filtered = applyPoolFilters(allItems, filters)
    return sortPoolItems(filtered, sortKey, sortDir)
  }, [allItems, filters, sortKey, sortDir])

  const summary = useMemo(() => computePoolSummary(allItems, result), [allItems, result])

  const handleSort = (key: CandidatePoolSortKey) => {
    if (key === sortKey) {
      setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    } else {
      setSortKey(key)
      setSortDir(key === 'ownership' || key === 'salary' ? 'asc' : 'desc')
    }
  }

  const setFilter = <K extends keyof CandidatePoolFilters>(k: K, v: CandidatePoolFilters[K]) =>
    setFilters(f => ({ ...f, [k]: v }))

  const clearFilters = () => setFilters({})

  const activeFilterCount = Object.values(filters).filter(v =>
    v !== undefined && v !== '' && v !== false,
  ).length

  return (
    <div className="bg-surface-overlay border border-surface-border rounded-xl overflow-hidden mt-1">
      {/* ---- Header / accordion toggle ---- */}
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-[11px] bg-transparent border-none cursor-pointer text-left"
      >
        <div className="flex items-center gap-2.5 flex-wrap">
          <span className="text-xs font-bold text-text-secondary uppercase tracking-[0.05em]">
            🔍 Candidate Pool Inspector
          </span>
          <span className="text-[11px] bg-[#1e3a5f] text-[#60a5fa] px-2 py-px rounded-lg font-bold">
            {summary.eligibleCount} legal
          </span>
          {summary.excludedCount > 0 && (
            <span className="text-[11px] bg-[#450a0a] text-[#fca5a5] px-2 py-px rounded-lg font-bold">
              {summary.excludedCount} excluded
            </span>
          )}
          {activeFilterCount > 0 && (
            <span className="text-[11px] bg-[#5b21b6] text-[#c4b5fd] px-2 py-px rounded-lg font-bold">
              {activeFilterCount} filter{activeFilterCount > 1 ? 's' : ''} active
            </span>
          )}
        </div>
        <span className="text-sm text-text-muted shrink-0 inline-block transition-transform duration-200" style={{ transform: open ? 'rotate(180deg)' : 'none' }}>▾</span>
      </button>

      {open && (
        <>
          {/* ---- Summary bar ---- */}
          <div className="px-4 py-2 bg-[#0a0f1a] border-t border-surface-border flex flex-wrap gap-4 items-center text-[11px] text-text-muted">
            <span><strong className="text-text-secondary">Lineup:</strong> {lineupLabel}</span>
            <span><strong className="text-text-secondary">Budget:</strong>{'  '}
              <span style={{ color: summary.remainingSalary < 4500 ? '#ef4444' : '#22c55e', fontWeight: 700 }}>
                ${summary.remainingSalary.toLocaleString()}
              </span>{' '}remaining
            </span>
            <span><strong className="text-text-secondary">Open Slots:</strong>{' '}
              {summary.openPositions.length > 0
                ? summary.openPositions.map(p => (
                  <span key={p} className="inline-block ml-[3px] px-[5px] py-px bg-surface-border text-[#a78bfa] rounded font-bold text-[10px]">{p}</span>
                ))
                : <span className="text-success">all filled</span>
              }
            </span>
            <span><strong className="text-success">{summary.eligibleCount}</strong> legal</span>
            <span><strong className="text-[#fca5a5]">{summary.excludedCount}</strong> excluded</span>
            <span><strong className="text-[#60a5fa]">{summary.fitSalaryCount}</strong> fit salary</span>
          </div>

          {/* ---- Toolbar ---- */}
          <div className="px-4 py-2 border-t border-surface-border bg-surface-overlay flex flex-wrap gap-2 items-center">
            {/* Search */}
            <input
              type="text"
              placeholder="Search player / team…"
              value={filters.search ?? ''}
              onChange={e => setFilter('search', e.target.value || undefined)}
              className="bg-surface-border text-text-primary border border-surface-border rounded px-2.5 py-[5px] text-xs outline-none w-[170px]"
            />

            {/* Position filter */}
            <select
              value={filters.position ?? ''}
              onChange={e => setFilter('position', e.target.value || undefined)}
              className="bg-surface-border text-text-secondary border border-surface-border rounded px-2 py-[5px] text-xs cursor-pointer outline-none"
            >
              <option value="">All Positions</option>
              {POSITION_OPTIONS.map(p => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>

            {/* Toggle pills */}
            {([
              ['includedOnly',    'Legal Only',       '#14532d', '#4ade80' ],
              ['excludedOnly',    'Excluded Only',    '#450a0a', '#fca5a5' ],
              ['fitSalaryOnly',   'Fits Salary',      '#1e3a5f', '#60a5fa' ],
              ['hideUserExcluded','Hide Excluded',    '#1e293b', '#64748b' ],
            ] as [keyof CandidatePoolFilters, string, string, string][]).map(([k, label, bg, color]) => (
              <button
                key={k}
                onClick={() => setFilter(k, !filters[k] || undefined)}
                className="px-2.5 py-1 rounded text-[11px] font-bold cursor-pointer border-none transition-all"
                style={{
                  background: filters[k] ? bg : '#1e293b',
                  color: filters[k] ? color : '#475569',
                }}
              >
                {label}
              </button>
            ))}

            {/* Sort */}
            <select
              value={sortKey}
              onChange={e => { setSortKey(e.target.value as CandidatePoolSortKey); setSortDir('desc') }}
              className="bg-surface-border text-text-secondary border border-surface-border rounded px-2 py-[5px] text-xs cursor-pointer outline-none ml-auto"
            >
              {SORT_OPTIONS.map(o => (
                <option key={o.key} value={o.key}>Sort: {o.label}</option>
              ))}
            </select>

            <button
              onClick={() => setSortDir(d => d === 'desc' ? 'asc' : 'desc')}
              title={`Currently: ${sortDir === 'desc' ? 'Descending' : 'Ascending'}`}
              className="px-2 py-[5px] rounded border border-surface-border bg-surface-border text-text-muted cursor-pointer text-[11px] hover:text-text-secondary transition-colors"
            >
              {sortDir === 'desc' ? '↓' : '↑'}
            </button>

            {activeFilterCount > 0 && (
              <button
                onClick={clearFilters}
                className="px-2 py-1 rounded border border-surface-border bg-transparent text-text-muted cursor-pointer text-[11px] hover:text-text-secondary transition-colors"
              >
                ✕ Clear
              </button>
            )}
          </div>

          {/* ---- Table ---- */}
          {filteredAndSorted.length === 0 ? (
            <div className="px-4 py-7 text-center text-surface-border text-sm">
              No candidates match the current filters.
            </div>
          ) : (
            <div className="overflow-x-auto max-h-[420px] overflow-y-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr>
                    {(
                      [
                        ['player',      'Player',        false],
                        ['positions',   'Pos',           false],
                        ['salary',      'Salary',        true ],
                        ['projection',  'Proj',          true ],
                        ['ceiling',     'Ceil',          true ],
                        ['ownership',   'Own%',          true ],
                        ['value',       'Value',         true ],
                        ['leverage',    'Lev',           true ],
                        ['swapScore',   'Score',         true ],
                        ['eligSlots',   'Slots',         false],
                        ['fitSalary',   'Fit $',         false],
                        ['status',      'Status',        false],
                        ['candidateFor','For',           false],
                        ['actions',     '',              false],
                      ] as [string, string, boolean][]
                    ).map(([key, label, sortable]) => (
                      <th
                        key={key}
                        className={thCls}
                        onClick={sortable ? () => handleSort(key as CandidatePoolSortKey) : undefined}
                      >
                        {label}
                        {sortable && <SortIcon active={sortKey === key} dir={sortDir} />}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filteredAndSorted.map((item, idx) => (
                    <CandidateRow
                      key={item.name}
                      item={item}
                      rank={idx + 1}
                      onUse={() => {
                        const scratched = item.candidateFor[0]
                        if (scratched) onUseCandidate(scratched, item.name, item.dfsId || undefined)
                      }}
                      onToggleExclude={() => onToggleExclude(item.name)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* ---- Footer count ---- */}
          <div className="px-4 py-1.5 border-t border-surface-base flex justify-between items-center text-[11px] text-surface-border">
            <span>{filteredAndSorted.length} of {allItems.length} candidates</span>
            {activeFilterCount > 0 && (
              <button onClick={clearFilters} className="text-[11px] text-text-muted bg-transparent border-none cursor-pointer hover:text-text-secondary">
                Clear filters
              </button>
            )}
          </div>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Individual row
// ---------------------------------------------------------------------------

function CandidateRow({
  item, rank, onUse, onToggleExclude,
}: {
  item: CandidatePoolItem
  rank: number
  onUse: () => void
  onToggleExclude: () => void
}) {
  const [showTip, setShowTip] = useState(false)
  const badge = getStatusBadge(item)
  const diagnostic = getCandidateDiagnostic(item)

  const rowBg = item.eligible
    ? rank % 2 === 0 ? 'transparent' : '#0a0f1a'
    : item.reasonExcluded === ExclusionReason.SALARY_TOO_HIGH
      ? '#1a0a00'
      : '#0d0505'

  const textColor = item.eligible ? '#cbd5e1' : '#475569'
  const nameColor = item.eligible
    ? '#f1f5f9'
    : item.reasonExcluded === ExclusionReason.USER_EXCLUDED
      ? '#7f1d1d'
      : '#475569'

  return (
    <tr
      className={cn(!item.eligible && 'opacity-70')}
      style={{ background: rowBg }}
      onMouseEnter={() => setShowTip(true)}
      onMouseLeave={() => setShowTip(false)}
    >
      {/* Player */}
      <td className={cn(tdCls, "min-w-[140px]")}>
        <div className="flex items-center gap-[5px]">
          <span className="font-bold" style={{ color: nameColor }}>
            {item.name}
          </span>
          {item.isSameTeam && (
            <span className="text-[8px] bg-success-muted text-success px-1 py-px rounded font-bold">STACK</span>
          )}
          {item.isSameGame && !item.isSameTeam && (
            <span className="text-[8px] bg-[#1e3a5f] text-[#38bdf8] px-1 py-px rounded font-bold">CORR</span>
          )}
        </div>
        <div className="text-[10px] text-text-muted mt-px">{item.team} · {item.gameInfo.split(' ')[0]}</div>
      </td>

      {/* Pos */}
      <td className={cn(tdCls, "text-text-secondary text-[11px]")}>
        {item.positions.join('/')}
      </td>

      {/* Salary */}
      <td className={cn(tdCls, item.fitsSalary ? "" : "text-[#f97316] font-bold")} style={{ color: item.fitsSalary ? textColor : undefined }}>
        {formatSalary(item.salary)}
      </td>

      {/* Proj */}
      <td className={cn(tdCls, "text-[#22d3ee] font-bold")}>
        {item.projection.toFixed(1)}
      </td>

      {/* Ceil */}
      <td className={cn(tdCls, "text-[#818cf8]")}>
        {item.ceiling.toFixed(1)}
      </td>

      {/* Own% */}
      <td className={tdCls} style={{ color: textColor }}>
        {item.ownership.toFixed(0)}%
      </td>

      {/* Value */}
      <td className={cn(tdCls, "text-[#a78bfa]")}>
        {item.value.toFixed(2)}
      </td>

      {/* Leverage */}
      <td className={tdCls} style={{ color: item.leverage > 15 ? '#22c55e' : item.leverage > 5 ? '#84cc16' : textColor }}>
        {item.leverage.toFixed(1)}
      </td>

      {/* Score */}
      <td className={cn(tdCls, "font-bold")} style={{ color: scoreColor(item.swapScore) }}>
        {(item.swapScore * 100).toFixed(0)}
      </td>

      {/* Eligible Slots */}
      <td className={cn(tdCls, "text-text-muted text-[11px]")}>
        {item.eligibleSlots.slice(0, 4).join(', ')}
      </td>

      {/* Fits Salary */}
      <td className={cn(tdCls, "text-center")}>
        {item.fitsSalary
          ? <span className="text-success font-bold text-sm">✓</span>
          : <span className="text-danger font-bold text-sm">✗</span>
        }
      </td>

      {/* Status badge */}
      <td className={tdCls}>
        <span className="text-[9px] font-bold px-[5px] py-0.5 rounded" style={{ background: badge.bg, color: badge.color }}>
          {badge.label}
        </span>
      </td>

      {/* Candidate-for */}
      <td className={cn(tdCls, "text-text-muted text-[11px] max-w-[120px] overflow-hidden text-ellipsis")}>
        <span title={item.candidateFor.join(', ')}>
          {item.candidateFor[0]?.split(' ').pop()}{item.candidateFor.length > 1 ? ` +${item.candidateFor.length - 1}` : ''}
        </span>
      </td>

      {/* Actions */}
      <td className={cn(tdCls, "px-1.5 py-1")}>
        <div className="flex gap-1">
          <button
            onClick={onUse}
            disabled={!item.eligible}
            title={item.eligible ? `Swap in ${item.name}` : diagnostic?.message}
            className={cn('px-2 py-[3px] rounded border-none text-[10px] font-bold transition-colors',
              item.eligible ? 'bg-primary text-white cursor-pointer' : 'bg-surface-border text-surface-border opacity-50 cursor-not-allowed'
            )}
          >
            Use
          </button>
          <button
            onClick={onToggleExclude}
            title={item.reasonExcluded === ExclusionReason.USER_EXCLUDED
              ? `Re-include ${item.name}`
              : `Exclude ${item.name} from pool`}
            className={cn('px-1.5 py-[3px] rounded text-[10px] font-bold cursor-pointer border transition-colors',
              item.reasonExcluded === ExclusionReason.USER_EXCLUDED ? 'border-[#7f1d1d] bg-[#450a0a] text-[#fca5a5]' : 'border-surface-border bg-transparent text-text-muted'
            )}
          >
            {item.reasonExcluded === ExclusionReason.USER_EXCLUDED ? '+' : '✕'}
          </button>
        </div>
        {/* Tooltip on exclusion reason */}
        {!item.eligible && diagnostic && showTip && (
          <div className="absolute z-50 bg-surface-border border border-surface-border rounded px-2.5 py-1.5 text-[11px] text-text-secondary max-w-[220px] pointer-events-none mt-0.5">
            {diagnostic.message}
          </div>
        )}
      </td>
    </tr>
  )
}

// ---------------------------------------------------------------------------
// Score colour helper (mirrors page.tsx)
// ---------------------------------------------------------------------------

function scoreColor(s: number): string {
  if (s >= 0.8) return '#22c55e'
  if (s >= 0.6) return '#84cc16'
  if (s >= 0.4) return '#facc15'
  if (s >= 0.2) return '#f97316'
  return '#ef4444'
}
