'use client'

import { useState, useEffect, useRef, useMemo } from 'react'
import {
  lateSwap,
  runFullPipeline,
  batchLateSwap,
  downloadLineups,
  downloadFile,
  type SwapCandidate,
  type SwapResult,
  type ParsedLineup,
  type FDOptimizerResponse,
  type FDLineup,
  type BatchLateSwapResponse,
  type BatchSwapLineupLog,
} from '@/lib/api'
import { formatSalary, cn } from '@/lib/utils'
import { useLatestSlate } from '@/hooks/useLatestSlate'
import { SlateSelector } from '@/components/shared/SlateSelector'
import { useInjuryEvents } from '@/hooks/useInjuryEvents'
import { SwapResultComparison } from '@/components/lateSwap/SwapResultComparison'
import { BatchSwapResultsTable } from '@/components/lateSwap/BatchSwapResultsTable'
import { CandidatePoolInspector } from '@/components/lateSwap/CandidatePoolInspector'
import type { LineupSwapComparison } from '@/lib/late-swap/types'
import { FailureReason } from '@/lib/late-swap/types'
import { classifySwapFailure, validateExportReadiness } from '@/lib/late-swap/diagnostics'
import type { FDLineupEntry } from '@/lib/late-swap/models/fdLineup'
import {
  importFanDuelEntriesSelfContained,
  parseCSVRow,
  readFileAsText,
} from '@/lib/late-swap/import/importFanDuelEntries'
import { exportFanDuelLineups, downloadCSV } from '@/lib/late-swap/export/exportFanDuelLineups'
import type { DKLineupEntry } from '@/lib/late-swap/models/dkLineup'
import { importDraftKingsEntries, detectCSVSite } from '@/lib/late-swap/import/importDraftKingsEntries'
import { exportDraftKingsLineups } from '@/lib/late-swap/export/exportDraftKingsLineups'
import {
  validateFDLineups,
  hasBlockingValidationErrors,
  blockingErrorSummary,
} from '@/lib/late-swap/validation/validateFDLineup'

// --- Types --------------------------------------------------------------------

type Site = 'DK' | 'FD'
type Mode = 'swap' | 'reoptimize'
type ContestMode = 'gpp' | 'cash' | 'single_entry'

interface ManagedLineup {
  id: number
  label: string
  players: string[]
  /** Snapshot of players at import time — never mutated after import. Used for before/after comparison. */
  originalPlayers: string[]
  slotLabels: string[]
  isAffected: boolean
  /**
   * FanDuel entry data with slot-bound player IDs and entry metadata.
   * Present only when site === 'FD' and the lineup was imported via the
   * client-side FD importer. Required for generating a valid FD upload CSV.
   */
  fdEntry?: FDLineupEntry
  /**
   * DraftKings entry data with slot-bound player IDs and entry metadata.
   * Present only when site === 'DK' and the lineup was imported via the
   * client-side DK importer. Required for generating a valid DK upload CSV.
   */
  dkEntry?: DKLineupEntry
}

// --- Helpers ------------------------------------------------------------------

function deltaColor(n: number): string {
  if (n > 2) return '#22c55e'
  if (n > 0) return '#84cc16'
  if (n < -2) return '#ef4444'
  if (n < 0) return '#f97316'
  return '#94a3b8'
}
function salaryDeltaColor(n: number): string {
  return n > 0 ? '#f97316' : n < 0 ? '#22c55e' : '#94a3b8'
}
function scoreColor(s: number): string {
  if (s >= 0.8) return '#22c55e'
  if (s >= 0.6) return '#84cc16'
  if (s >= 0.4) return '#facc15'
  if (s >= 0.2) return '#f97316'
  return '#ef4444'
}

function getPreferredActiveLineupIndex(lineups: ManagedLineup[], fallbackIndex = 0): number {
  if (lineups.length === 0) return 0
  const firstAffectedIndex = lineups.findIndex(lineup => lineup.isAffected)
  if (firstAffectedIndex >= 0) return firstAffectedIndex
  return Math.min(fallbackIndex, lineups.length - 1)
}

type SortKey = 'swap_score' | 'projection' | 'ceiling' | 'value' | 'own' | 'proj_delta' | 'salary'
type SortDir = 'desc' | 'asc'

// --- Style constants ----------------------------------------------------------

const tdCls = 'px-2.5 py-[7px] text-xs border-b border-surface-border text-[#cbd5e1] whitespace-nowrap'
const thCls = 'px-2.5 py-[7px] text-[10px] font-bold uppercase tracking-wide text-text-muted border-b-2 border-surface-border text-left whitespace-nowrap bg-surface-base sticky top-0 cursor-pointer select-none'
const cardCls = 'bg-surface-overlay border border-surface-border rounded-xl p-5 mb-4'
const lblCls = 'block text-[11px] text-[#9ca3af] uppercase mb-1 font-semibold'
const inpCls = 'bg-surface-border text-text-primary border border-surface-border rounded px-2.5 py-1.5 text-sm outline-none w-full box-border'
const secBtnCls = 'px-3.5 py-[7px] rounded-[7px] border border-surface-border text-sm font-semibold cursor-pointer bg-transparent text-text-secondary hover:bg-surface-overlay transition-colors'

// --- Slot configuration -------------------------------------------------------

const FD_SLOTS = ['PG', 'PG', 'SG', 'SG', 'SF', 'SF', 'PF', 'PF', 'C'] as const
const DK_SLOTS = ['PG', 'SG', 'SF', 'PF', 'C', 'G', 'F', 'UTIL'] as const

const DK_PREVIEW_SLOTS = ['PG', 'SG', 'SF', 'PF', 'C', 'G', 'F', 'UTIL'] as const
const FD_PREVIEW_SLOTS = ['PG', 'PG_2', 'SG', 'SG_2', 'SF', 'SF_2', 'PF', 'PF_2', 'C'] as const
const FD_PREVIEW_LABELS = ['PG', 'PG', 'SG', 'SG', 'SF', 'SF', 'PF', 'PF', 'C']

// --- Swap candidate row --------------------------------------------------------

function CandidateRow({
  c, rank, onUse, onUseAll, multiLineupMode,
}: {
  c: SwapCandidate
  rank: number
  onUse: () => void
  onUseAll?: () => void
  multiLineupMode?: boolean
}) {
  return (
    <tr className={rank % 2 === 0 ? '' : 'bg-surface-raised/20'}>
      <td className={tdCls}>{rank}</td>
      <td className={cn(tdCls, 'font-bold text-text-primary min-w-[130px]')}>
        <div className="flex items-center gap-1.5">
          {c.name}
          {c.is_same_game && !c.is_same_team && (
            <span title="Same game" className="text-[9px] bg-primary-muted text-[#38bdf8] px-1 py-px rounded font-semibold">CORR</span>
          )}
          {c.is_same_team && (
            <span title="Same team" className="text-[9px] bg-success-muted text-success px-1 py-px rounded font-semibold">STACK</span>
          )}
        </div>
      </td>
      <td className={cn(tdCls, 'text-text-secondary')}>{c.position}</td>
      <td className={cn(tdCls, 'text-text-muted text-[11px]')}>{c.team}</td>
      <td className={tdCls}>{formatSalary(c.salary)}</td>
      <td className={cn(tdCls, 'font-bold text-[#22d3ee]')}>{c.projection.toFixed(1)}</td>
      <td className={cn(tdCls, 'text-[#818cf8]')}>{c.ceiling.toFixed(1)}</td>
      <td className={cn(tdCls, 'text-[#a78bfa]')}>{c.value.toFixed(2)}</td>
      <td className={cn(tdCls, 'font-bold')} style={{ color: deltaColor(c.proj_delta) }}>
        {c.proj_delta > 0 ? '+' : ''}{c.proj_delta.toFixed(1)}
      </td>
      <td className={tdCls} style={{ color: salaryDeltaColor(c.salary_delta) }}>
        {c.salary_delta > 0 ? '+' : ''}{c.salary_delta.toLocaleString()}
      </td>
      <td className={cn(tdCls, 'text-text-muted')}>{c.new_total_salary.toLocaleString()}</td>
      <td className={cn(tdCls, 'text-[#475569]')}>{c.own.toFixed(0)}%</td>
      <td className={cn(tdCls, 'font-bold')} style={{ color: scoreColor(c.swap_score) }}>{(c.swap_score * 100).toFixed(0)}</td>
      <td className={cn(tdCls, 'px-2 py-1')}>
        <div className="flex gap-1 flex-nowrap">
          <button
            onClick={onUse}
            title="Apply swap to this lineup"
            className="px-2 py-[3px] rounded border-none text-[11px] font-bold cursor-pointer bg-primary text-white whitespace-nowrap hover:bg-primary-hover transition-colors"
          >
            Use ✓
          </button>
          {multiLineupMode && onUseAll && (
            <button
              onClick={onUseAll}
              title="Apply to ALL lineups containing this scratched player"
              className="px-2 py-[3px] rounded border-none text-[11px] font-bold cursor-pointer bg-[#7c3aed] text-white whitespace-nowrap hover:bg-[#6d28d9] transition-colors"
            >
              All ✓✓
            </button>
          )}
        </div>
      </td>
    </tr>
  )
}

// --- Swap block for one scratched player --------------------------------------

function SwapBlock({
  swap, onUseCandidate, onUseCandidateAll, multiLineupMode,
}: {
  swap: SwapResult
  onUseCandidate: (scratched: string, candidateName: string, dfsId?: string) => void
  onUseCandidateAll?: (scratched: string, candidateName: string, dfsId?: string) => void
  multiLineupMode?: boolean
}) {
  const [sortKey, setSortKey] = useState<SortKey>('swap_score')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [showAll, setShowAll] = useState(false)

  const sortableCols: { key: SortKey; label: string }[] = [
    { key: 'swap_score', label: 'Score' },
    { key: 'projection', label: 'Proj' },
    { key: 'ceiling', label: 'Ceil' },
    { key: 'value', label: 'Value' },
    { key: 'proj_delta', label: '+/-FPTS' },
    { key: 'salary', label: 'Salary' },
    { key: 'own', label: 'Own%' },
  ]

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    } else {
      setSortKey(key)
      setSortDir(key === 'own' ? 'asc' : 'desc')
    }
  }

  const sorted = [...swap.candidates].sort((a, b) => {
    const av = a[sortKey] as number
    const bv = b[sortKey] as number
    return sortDir === 'desc' ? bv - av : av - bv
  })
  const displayed = showAll ? sorted : sorted.slice(0, 8)

  return (
    <div className="bg-surface-overlay border border-surface-border rounded-xl overflow-hidden mb-4">
      <div className="px-4 py-3 border-b border-surface-border flex gap-5 items-center flex-wrap bg-surface-base">
        <div>
          <span className="text-[10px] text-text-muted uppercase font-semibold">Scratched</span>
          <div className="text-[15px] font-extrabold text-danger mt-0.5">
            {swap.resolved_player !== swap.scratched_player
              ? <>{swap.scratched_player} <span className="text-[11px] text-text-muted font-normal">to {swap.resolved_player}</span></>
              : swap.scratched_player}
          </div>
        </div>
        <div>
          <span className="text-[10px] text-text-muted uppercase font-semibold">Pos</span>
          <div className="text-sm font-bold text-text-secondary mt-0.5">{swap.scratched_position}</div>
        </div>
        <div>
          <span className="text-[10px] text-text-muted uppercase font-semibold">Salary</span>
          <div className="text-sm font-bold text-text-secondary mt-0.5">{formatSalary(swap.scratched_salary)}</div>
        </div>
        <div>
          <span className="text-[10px] text-text-muted uppercase font-semibold">Budget</span>
          <div className="text-sm font-bold text-success mt-0.5">{formatSalary(swap.salary_budget)}</div>
        </div>
        <div>
          <span className="text-[10px] text-text-muted uppercase font-semibold">Their Proj</span>
          <div className="text-sm font-bold text-[#22d3ee] mt-0.5">{swap.scratched_proj.toFixed(1)}</div>
        </div>

        {multiLineupMode && (
          <div className="px-2.5 py-1 bg-[#2d1b69] border border-[#7c3aed33] rounded text-[11px] text-[#c4b5fd]">
            <strong>All ✓✓</strong> applies to every lineup containing this player
          </div>
        )}

        <div className="flex items-center gap-1.5 ml-auto flex-wrap">
          <span className="text-[10px] text-[#475569]">Sort:</span>
          {sortableCols.map(sc => (
            <button
              key={sc.key}
              onClick={() => handleSort(sc.key)}
              className={cn('px-2 py-[3px] rounded border-none text-[11px] font-semibold cursor-pointer transition-colors',
                sortKey === sc.key ? 'bg-primary-muted text-[#38bdf8]' : 'bg-surface-base text-[#475569] hover:text-text-secondary'
              )}
            >
              {sc.label}{sortKey === sc.key ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''}
            </button>
          ))}
          <span className={cn('px-2 py-[3px] rounded text-[11px] font-bold ml-1',
            swap.candidates.length > 0 ? 'bg-success-muted text-success' : 'bg-danger-muted text-danger'
          )}>
            {swap.total_candidates_found} found
          </span>
        </div>
      </div>

      {swap.candidates.length === 0 ? (
        (() => {
          const diag = classifySwapFailure(swap)
          return (
            <div className="p-4">
              <div className="bg-danger-muted border border-danger/20 rounded-lg px-4 py-3">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-sm">⚠</span>
                  <span className="text-xs font-bold text-danger uppercase">
                    No Replacements — {diag.label}
                  </span>
                </div>
                <div className="text-xs text-[#fca5a5] mb-2.5">{diag.message}</div>
                <div className="flex flex-col gap-1">
                  {diag.suggestions.map((s, i) => (
                    <div key={i} className="text-[11px] text-text-secondary flex gap-1.5">
                      <span className="text-text-muted">•</span><span>{s}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )
        })()
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  {['#', 'Player', 'Pos', 'Team', 'Salary', 'Proj', 'Ceil', 'Value', '+/-FPTS', '+/-$', 'New$', 'Own%', 'Score', ''].map((h, i) => {
                    const sortableMap: Record<string, SortKey> = {
                      'Salary': 'salary', 'Proj': 'projection', 'Ceil': 'ceiling',
                      'Value': 'value', '+/-FPTS': 'proj_delta', 'Own%': 'own', 'Score': 'swap_score',
                    }
                    const sk = sortableMap[h]
                    return (
                      <th
                        key={i}
                        className={cn(thCls, sk && sortKey === sk ? 'text-[#38bdf8]' : '')}
                        onClick={sk ? () => handleSort(sk) : undefined}
                        title={sk ? `Sort by ${h}` : undefined}
                      >
                        {h}{sk && sortKey === sk ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''}
                      </th>
                    )
                  })}
                </tr>
              </thead>
              <tbody>
                {displayed.map((c, i) => (
                  <CandidateRow
                    key={c.name}
                    c={c}
                    rank={i + 1}
                    onUse={() => onUseCandidate(swap.resolved_player, c.name, c.dfs_id || undefined)}
                    onUseAll={onUseCandidateAll ? () => onUseCandidateAll(swap.resolved_player, c.name, c.dfs_id || undefined) : undefined}
                    multiLineupMode={multiLineupMode}
                  />
                ))}
              </tbody>
            </table>
          </div>
          {swap.candidates.length > 8 && (
            <div className="px-4 py-2 border-t border-surface-border text-center">
              <button
                onClick={() => setShowAll(v => !v)}
                className="px-3 py-1 rounded border border-surface-border text-xs font-semibold cursor-pointer bg-transparent text-text-muted hover:text-text-secondary transition-colors"
              >
                {showAll ? 'Show fewer' : `Show all ${swap.candidates.length}`}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// --- Multi-Lineup Manager -----------------------------------------------------

function MultiLineupManager({
  lineups, activeIdx, onSelect,
}: {
  lineups: ManagedLineup[]
  activeIdx: number
  onSelect: (idx: number) => void
}) {
  const affectedCount = lineups.filter(lu => lu.isAffected).length
  return (
    <div className={cn(cardCls, 'p-0 overflow-hidden')}>
      <div className="px-4 py-2.5 border-b border-surface-border flex items-center justify-between bg-surface-base">
        <div className="text-sm font-bold text-text-primary">
          Lineup Manager
          <span className="ml-2 text-[11px] text-text-muted font-normal">
            {lineups.length} lineups imported
          </span>
        </div>
        <div className="flex gap-2 items-center">
          {affectedCount > 0 && (
            <span className="text-[11px] font-bold text-[#fca5a5] bg-danger-muted border border-danger/20 px-2 py-0.5 rounded">
              {affectedCount} affected
            </span>
          )}
          {affectedCount === 0 && (
            <span className="text-[11px] font-bold text-success bg-success-muted border border-success/20 px-2 py-0.5 rounded">
              All clean
            </span>
          )}
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5 p-2.5 px-4 max-h-[130px] overflow-y-auto">
        {lineups.map((lu, i) => (
          <button
            key={i}
            onClick={() => onSelect(i)}
            title={lu.isAffected ? 'Contains a scratched player' : 'Click to edit'}
            className={cn('px-2.5 py-1 rounded text-xs font-semibold cursor-pointer transition-colors',
              i === activeIdx ? 'bg-primary text-white border border-transparent'
              : lu.isAffected ? 'bg-[#2d0a0a] text-[#fca5a5] border border-danger/30'
              : 'bg-surface-border text-text-secondary border border-transparent hover:bg-surface-overlay'
            )}
          >
            {lu.label}{lu.isAffected ? ' !' : ''}
          </button>
        ))}
      </div>
    </div>
  )
}

// --- Lineup Builder (slot inputs + paste area) --------------------------------

function LineupBuilder({ site, lineupText, setLineupText, scratchedText, setScratchedText }: {
  site: Site
  lineupText: string
  setLineupText: (s: string) => void
  scratchedText: string
  setScratchedText: (s: string) => void
}) {
  const slots = site === 'FD' ? FD_SLOTS : DK_SLOTS
  const lines = lineupText.split('\n').map(l => l.trim())
  const scratchedNames = scratchedText.split('\n').map(s => s.trim().toLowerCase()).filter(Boolean)

  return (
    <div className="flex gap-4 flex-wrap">
      <div className="flex-[2] min-w-[260px]">
        <div className="text-[11px] text-text-muted uppercase font-semibold mb-2">
          {site} Lineup ({slots.length} players)
        </div>
        <div className="flex flex-col gap-1.5">
          {slots.map((slot, i) => {
            const val = lines[i] ?? ''
            const isScratched = val && scratchedNames.includes(val.toLowerCase())
            return (
              <div key={i} className="flex items-center gap-2">
                <span className="w-9 text-[11px] font-bold text-text-muted uppercase shrink-0 text-right">{slot}</span>
                <input
                  type="text"
                  placeholder={`Player ${i + 1}`}
                  value={val}
                  onChange={e => {
                    const newLines = [...lines]
                    while (newLines.length <= i) newLines.push('')
                    newLines[i] = e.target.value
                    setLineupText(newLines.join('\n'))
                  }}
                  className={cn(inpCls,
                    isScratched ? 'border-danger bg-[#2d0a0a] text-[#fca5a5]' : ''
                  )}
                />
                {isScratched && (
                  <span title="Player is scratched" className="text-xs text-danger shrink-0">✕</span>
                )}
              </div>
            )
          })}
        </div>
        <div className="mt-2 text-[11px] text-surface-border">
          Or paste all names (one per line) into the text area
        </div>
      </div>
      <div className="flex-1 min-w-[220px]">
        <div className="text-[11px] text-text-muted uppercase font-semibold mb-2">
          Paste Lineup (one name per line)
        </div>
        <textarea
          value={lineupText}
          onChange={e => setLineupText(e.target.value)}
          placeholder={'LeBron James\nStephen Curry\nNikola Jokic\n...'}
          className={cn(inpCls, 'h-[220px] resize-y font-mono text-xs')}
        />
        <div className="mt-3 text-[11px] text-text-muted uppercase font-semibold mb-2">
          Scratched Players (one per line)
        </div>
        <textarea
          value={scratchedText}
          onChange={e => setScratchedText(e.target.value)}
          placeholder={'Player who got scratched'}
          className={cn(inpCls, 'h-20 resize-y font-mono text-xs border-[#7f1d1d]')}
        />
      </div>
    </div>
  )
}

// --- Lineup Slot Grid ----------------------------------------------------------

function LineupSlotGrid({
  lineup, site, scratchedNamesSet, lockedNames, onScratch,
}: {
  lineup: ManagedLineup
  site: Site
  scratchedNamesSet: Set<string>
  lockedNames: string[]
  onScratch?: (playerName: string) => void
}) {
  const lockedSet = new Set(lockedNames.map(n => n.toLowerCase()))
  const slots = lineup.slotLabels.length > 0
    ? lineup.slotLabels
    : (site === 'FD' ? Array.from(FD_SLOTS) : Array.from(DK_SLOTS))

  return (
    <div className="flex flex-col gap-[3px]">
      {lineup.players.map((player, i) => {
        const slot = slots[i] ?? `P${i + 1}`
        const isScratched = scratchedNamesSet.has(player.toLowerCase())
        const isLocked = lockedSet.has(player.toLowerCase())
        return (
          <div
            key={i}
            className={cn(
              'flex items-center gap-2 px-2.5 py-[7px] rounded border',
              isScratched ? 'bg-[#2d0a0a] border-danger/20'
              : isLocked ? 'bg-[#0c1f3a] border-primary/20'
              : 'bg-surface-border border-surface-border'
            )}
          >
            <span className={cn('w-9 text-[10px] font-bold uppercase shrink-0 text-right',
              isScratched ? 'text-danger' : isLocked ? 'text-primary' : 'text-text-muted'
            )}>
              {slot}
            </span>
            <span className={cn('flex-1 text-sm font-semibold',
              isScratched ? 'text-[#fca5a5]' : isLocked ? 'text-[#93c5fd]' : 'text-text-primary'
            )}>
              {player}
            </span>
            {isLocked && !isScratched && (
              <span className="text-[9px] bg-primary-muted text-[#60a5fa] px-1.5 py-px rounded font-bold shrink-0">🔒 LOCK</span>
            )}
            {isScratched && (
              <span className="text-[9px] bg-[#7f1d1d] text-[#fca5a5] px-1.5 py-px rounded font-bold shrink-0">❌ OUT</span>
            )}
            {!isScratched && !isLocked && (
              <span className="text-[9px] bg-success-muted text-success px-1.5 py-px rounded font-bold shrink-0 opacity-70">✓</span>
            )}
            {!isScratched && onScratch && (
              <button
                onClick={() => onScratch(player)}
                title={isLocked ? 'Mark as scratched (removes from locked list)' : 'Mark as scratched'}
                className="text-[10px] px-1.5 py-px rounded border border-danger/30 text-[#f87171] bg-transparent cursor-pointer hover:bg-[#2d0a0a] transition-colors shrink-0 font-semibold ml-0.5"
              >
                Scratch
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}

// --- Exposure Panel -----------------------------------------------------------

function ExposurePanel({
  lineups, scratchedNamesSet, lockedNames,
}: {
  lineups: ManagedLineup[]
  scratchedNamesSet: Set<string>
  lockedNames: string[]
}) {
  const [open, setOpen] = useState(false)
  const lockedSet = new Set(lockedNames.map(n => n.toLowerCase()))

  const exposureEntries = useMemo(() => {
    const map = new Map<string, number>()
    for (const lu of lineups) {
      for (const p of lu.players) {
        const key = p.trim()
        if (!key) continue
        map.set(key, (map.get(key) ?? 0) + 1)
      }
    }
    return [...map.entries()].sort((a, b) => b[1] - a[1])
  }, [lineups])

  const total = lineups.length
  const riskCount = exposureEntries.filter(([name]) => scratchedNamesSet.has(name.toLowerCase())).length

  if (total === 0) return null

  return (
    <div className="bg-surface-overlay border border-surface-border rounded-xl overflow-hidden mt-4">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-[18px] py-[11px] bg-transparent border-none cursor-pointer text-left"
      >
        <div className="flex items-center gap-2.5 flex-wrap">
          <span className="text-xs font-bold text-text-secondary uppercase tracking-[0.05em]">Exposure Summary</span>
          <span className="text-[11px] text-text-muted">{total} lineup{total !== 1 ? 's' : ''} · {exposureEntries.length} unique players</span>
          {riskCount > 0 && (
            <span className="text-[11px] bg-[#450a0a] text-[#fca5a5] px-2 py-px rounded-full font-bold">
              ⚠ {riskCount} scratched with exposure
            </span>
          )}
        </div>
        <span
          className={cn('text-[13px] text-text-muted shrink-0 inline-block transition-transform duration-200', open && 'rotate-180')}
        >▾</span>
      </button>
      {open && (
        <div className="px-[18px] pb-4 border-t border-surface-border">
          <div className="grid gap-[5px] mt-3 [grid-template-columns:repeat(auto-fill,minmax(260px,1fr))]">
            {exposureEntries.map(([name, count]) => {
              const isScratched = scratchedNamesSet.has(name.toLowerCase())
              const isLocked = lockedSet.has(name.toLowerCase())
              const pct = Math.round(count / total * 100)
              return (
                <div
                  key={name}
                  className={cn(
                    'flex items-center gap-2 px-2 py-[5px] rounded border text-xs',
                    isScratched ? 'bg-[#1f0a0a] border-danger/20' : 'bg-surface-base border-surface-border'
                  )}
                >
                  <span className={cn(
                    'flex-1 font-semibold text-xs overflow-hidden text-ellipsis whitespace-nowrap',
                    isScratched ? 'text-[#fca5a5]' : isLocked ? 'text-[#93c5fd]' : 'text-text-secondary'
                  )}>
                    {isScratched && <span>⚠&nbsp;</span>}{isLocked && <span>🔒&nbsp;</span>}{name}
                  </span>
                  <div className="w-[60px] h-1 bg-surface-border rounded shrink-0">
                    <div
                      className={cn('h-full rounded', isScratched ? 'bg-danger' : isLocked ? 'bg-primary' : 'bg-success')}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <span className="text-[11px] text-text-muted shrink-0 min-w-[52px] text-right">{count}/{total} ({pct}%)</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

// --- Re-Optimize Panel --------------------------------------------------------

function ReOptimizePanel({
  file, site, excludedPlayers, loading, result, error, onRun, onDownload,
}: {
  file: File | null
  site: Site
  excludedPlayers: string[]
  loading: boolean
  result: FDOptimizerResponse | null
  error: string | null
  onRun: (p: { numLineups: number; contestMode: ContestMode; enableStacking: boolean; minGameStack: number; bringBackCount: number }) => void
  onDownload: () => void
}) {
  const [numLineups, setNumLineups] = useState(20)
  const [contestMode, setContestMode] = useState<ContestMode>('gpp')
  const [enableStacking, setEnableStacking] = useState(true)
  const [minGameStack, setMinGameStack] = useState(2)
  const [bringBackCount, setBringBackCount] = useState(1)

  const previewSlotKeys = site === 'FD' ? FD_PREVIEW_SLOTS : DK_PREVIEW_SLOTS
  const previewLabels = site === 'FD' ? FD_PREVIEW_LABELS : Array.from(DK_PREVIEW_SLOTS)

  return (
    <div>
      <div className={cardCls}>
        <div className="text-sm font-bold text-text-primary mb-1">
          Re-Optimize Settings
        </div>
        <div className="text-xs text-text-muted mb-4">
          Runs the full DFS pipeline with scratched players excluded � builds entirely new lineups ready for upload.
        </div>

        {excludedPlayers.length > 0 && (
          <div className="mb-3.5 px-3 py-2 bg-[#1f0a0a] border border-danger/20 rounded-lg text-xs">
            <span className="text-danger font-bold">Excluded from pool: </span>
            <span className="text-[#fca5a5]">{excludedPlayers.join(', ')}</span>
          </div>
        )}
        {excludedPlayers.length === 0 && (
          <div className="mb-3.5 px-3 py-2 bg-surface-base border border-warning/20 rounded-lg text-xs text-warning">
            No scratched players set � add players to the scratch list for optimal re-optimize results.
          </div>
        )}

        <div className="grid gap-3 mb-4 [grid-template-columns:repeat(auto-fill,minmax(150px,1fr))]">
          <div>
            <label className={lblCls}>Lineups to Build</label>
            <input type="number" min={1} max={150} value={numLineups}
              onChange={e => setNumLineups(Number(e.target.value))} className={inpCls} />
          </div>
          <div>
            <label className={lblCls}>Contest Mode</label>
            <select value={contestMode} onChange={e => setContestMode(e.target.value as ContestMode)}
              className={cn(inpCls, 'cursor-pointer')}>
              <option value="gpp">GPP / Tournament</option>
              <option value="cash">Cash / 50/50</option>
              <option value="single_entry">Single Entry</option>
            </select>
          </div>
          <div className="flex items-center gap-2 pt-5">
            <input type="checkbox" id="reopt-stack" checked={enableStacking}
              onChange={e => setEnableStacking(e.target.checked)}
              className="accent-primary w-3.5 h-3.5 shrink-0" />
            <label htmlFor="reopt-stack" className="text-xs text-text-secondary cursor-pointer">
              Enable Stacking
            </label>
          </div>
          {enableStacking && (
            <>
              <div>
                <label className={lblCls}>Min Game Stack</label>
                <input type="number" min={0} max={6} value={minGameStack}
                  onChange={e => setMinGameStack(Number(e.target.value))} className={inpCls} />
              </div>
              <div>
                <label className={lblCls}>Bring-Back Count</label>
                <input type="number" min={0} max={4} value={bringBackCount}
                  onChange={e => setBringBackCount(Number(e.target.value))} className={inpCls} />
              </div>
            </>
          )}
        </div>

        <div className="flex gap-2.5 items-center flex-wrap">
          <button
            onClick={() => onRun({ numLineups, contestMode, enableStacking, minGameStack, bringBackCount })}
            disabled={!file || loading}
            className={cn('px-6 py-[9px] rounded-lg border-none text-sm font-bold text-white cursor-pointer transition-colors',
              !file || loading ? 'bg-surface-border opacity-60 cursor-not-allowed' : 'bg-[#059669] hover:bg-[#047857]'
            )}
          >
            {loading ? 'Building Lineups...' : `Build ${numLineups} New Lineup${numLineups !== 1 ? 's' : ''}`}
          </button>
          {!file && <span className="text-xs text-danger">No slate loaded</span>}
        </div>
      </div>

      {error && (
        <div className="px-4 py-2.5 bg-[#450a0a] border border-danger rounded-lg text-[#fca5a5] text-sm mb-4">
          {error}
        </div>
      )}

      {result && (
        <div className={cardCls}>
          <div className="flex justify-between items-center mb-3.5 flex-wrap gap-2.5">
            <div className="text-sm font-bold text-success">
              {result.total_lineups} Lineups Built
            </div>
            <button
              onClick={onDownload}
              disabled={!result.download_file}
              className={cn('px-4 py-[7px] rounded-lg border-none text-sm font-bold text-success cursor-pointer transition-colors',
                !result.download_file ? 'bg-surface-border opacity-50 cursor-not-allowed' : 'bg-[#166534] hover:bg-[#14532d]'
              )}
            >
              Download {site} Upload CSV
            </button>
          </div>

          {result.stats && (
            <div className="flex gap-3 mb-4 flex-wrap">
              {[
                ['Avg Projection', result.stats.avg_projection != null ? result.stats.avg_projection.toFixed(1) + ' pts' : '--'],
                ['Avg Salary', result.stats.avg_salary != null ? '$' + Math.round(result.stats.avg_salary).toLocaleString() : '--'],
                ['Range', result.stats.projection_range ?? '--'],
              ].map(([label, val]) => (
                <div key={label} className="bg-surface-base border border-surface-border rounded-lg px-3.5 py-2">
                  <div className="text-[10px] text-text-muted uppercase font-bold">{label}</div>
                  <div className="text-base font-extrabold text-text-primary mt-0.5">{val}</div>
                </div>
              ))}
            </div>
          )}

          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr>
                  <th className={thCls}>#</th>
                  {previewLabels.map((label, i) => <th key={i} className={thCls}>{label}</th>)}
                  <th className={cn(thCls, 'text-right')}>Salary</th>
                  <th className={cn(thCls, 'text-right text-success')}>Proj</th>
                  <th className={cn(thCls, 'text-right')}>Own%</th>
                </tr>
              </thead>
              <tbody>
                {result.lineups?.slice(0, 25).map((lineup: FDLineup, i: number) => (
                  <tr key={lineup.lineup_num} className={i % 2 === 0 ? 'bg-surface-raised/20' : ''}>
                    <td className={tdCls}>{lineup.lineup_num}</td>
                    {previewSlotKeys.map((key, ki) => (
                      <td key={ki} className={cn(tdCls, 'max-w-[120px] overflow-hidden text-ellipsis')}>
                        {lineup[key] != null ? String(lineup[key]) : '--'}
                      </td>
                    ))}
                    <td className={cn(tdCls, 'text-right')}>{formatSalary(lineup.total_salary)}</td>
                    <td className={cn(tdCls, 'text-right text-success font-bold')}>
                      {lineup.projected_points?.toFixed(1)}
                    </td>
                    <td className={cn(tdCls, 'text-right font-bold')}
                      style={{ color: lineup.total_ownership == null ? '#475569' : lineup.total_ownership < 150 ? '#34d399' : lineup.total_ownership < 200 ? '#facc15' : '#f87171' }}>
                      {lineup.total_ownership != null ? `${lineup.total_ownership.toFixed(1)}%` : '�'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {(result.lineups?.length ?? 0) > 25 && (
            <div className="py-2.5 text-center text-text-muted text-xs">
              +{(result.lineups?.length ?? 0) - 25} more lineups in the downloaded CSV
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// --- Page ---------------------------------------------------------------------

export default function LateSwapPage() {
  const [site, setSite] = useState<Site>('DK')
  const [mode, setMode] = useState<Mode>('swap')

  // Swap mode
  const [lineupText, setLineupText] = useState('')
  const [scratchedText, setScratchedText] = useState('')
  const [lockedText, setLockedText] = useState('')  // players in started games � force-kept
  const [file, setFile] = useState<File | null>(null)
  const [isSlateOverride, setIsSlateOverride] = useState(false)
  const [entryTemplateFileName, setEntryTemplateFileName] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{
    swaps: SwapResult[]
    current_salary: number
    salary_cap: number
    has_sim_data: boolean
    has_floor_data: boolean
    scoring_weights?: { w_proj: number; w_value: number; w_own: number }
  } | null>(null)

  // Controls
  const [randomness, setRandomness] = useState(0)
  const [nCandidates, setNCandidates] = useState(15)
  const [sortBy, setSortBy] = useState<'swap_score' | 'projection' | 'value' | 'ceiling' | 'own' | 'floor'>('swap_score')

  // Scoring controls — Phase 1.1 + 1.2
  type ContestType = 'balanced' | 'cash' | 'gpp'
  const WEIGHT_PRESETS: Record<ContestType, { w_proj: number; w_value: number; w_own: number }> = {
    balanced:  { w_proj: 0.50, w_value: 0.30, w_own: 0.20 },
    cash:      { w_proj: 0.70, w_value: 0.20, w_own: 0.10 },
    gpp:       { w_proj: 0.30, w_value: 0.20, w_own: 0.50 },
  }
  const [contestType, setContestType] = useState<ContestType>('balanced')
  const [wProj, setWProj] = useState(0.50)
  const [wValue, setWValue] = useState(0.30)
  const [wOwn, setWOwn] = useState(0.20)
  const [diversityFactor, setDiversityFactor] = useState(0.3)
  const weightSum = Math.round((wProj + wValue + wOwn) * 100) / 100
  const weightsValid = Math.abs(weightSum - 1.0) < 0.02

  const applyPreset = (preset: ContestType) => {
    setContestType(preset)
    const { w_proj, w_value, w_own } = WEIGHT_PRESETS[preset]
    setWProj(w_proj); setWValue(w_value); setWOwn(w_own)
  }

  // Swap history for undo — Phase 1.4
  interface SwapHistoryEntry {
    lineupIdx: number
    removedPlayer: string
    addedPlayer: string
    prevLineupText: string
    prevManagedLineup: ManagedLineup
    prevScratchedText: string
  }
  const [swapHistory, setSwapHistory] = useState<SwapHistoryEntry[]>([])

  const handleUndo = () => {
    setSwapHistory(prev => {
      if (prev.length === 0) return prev
      const entry = prev[prev.length - 1]
      setLineupText(entry.prevLineupText)
      setScratchedText(entry.prevScratchedText)
      setManagedLineups(lineups => lineups.map((lu, i) =>
        i === entry.lineupIdx ? entry.prevManagedLineup : lu
      ))
      setResult(null)
      return prev.slice(0, -1)
    })
  }

  const handleResetToOriginal = () => {
    setManagedLineups(prev => prev.map(lu => ({
      ...lu,
      players: [...lu.originalPlayers],
      fdEntry: lu.fdEntry ? {
        ...lu.fdEntry,
        slots: lu.fdEntry.slots.map((s, i) => ({
          ...s,
          playerName: lu.originalPlayers[i] ?? s.playerName,
        })),
      } : undefined,
      dkEntry: lu.dkEntry ? {
        ...lu.dkEntry,
        slots: lu.dkEntry.slots.map((s, i) => ({
          ...s,
          playerName: lu.originalPlayers[i] ?? s.playerName,
        })),
      } : undefined,
      isAffected: lu.originalPlayers.some(p => scratchedNamesSet.has(p.toLowerCase())),
    })))
    if (managedLineups[activeLineupIdx]) {
      setLineupText(managedLineups[activeLineupIdx].originalPlayers.join('\n'))
    }
    setSwapHistory([])
    setSwapComparisons(new Map())
    setResult(null)
  }

  // Multi-lineup management
  const [managedLineups, setManagedLineups] = useState<ManagedLineup[]>([])
  const [activeLineupIdx, setActiveLineupIdx] = useState(0)

  // Import
  const importRef = useRef<HTMLInputElement>(null)
  const slateOverrideRef = useRef<HTMLInputElement>(null)
  const injuryRef = useRef<HTMLDivElement>(null)
  const [importLoading, setImportLoading] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)
  const [compatibilityWarning, setCompatibilityWarning] = useState<string | null>(null)
  const [showSlateOverridePicker, setShowSlateOverridePicker] = useState(false)
  const [parsedLineups, setParsedLineups] = useState<ParsedLineup[] | null>(null)

  // Re-optimize
  const [reoptLoading, setReoptLoading] = useState(false)
  const [reoptResult, setReoptResult] = useState<FDOptimizerResponse | null>(null)
  const [reoptError, setReoptError] = useState<string | null>(null)

  // Batch auto-swap
  const [batchLoading, setBatchLoading] = useState(false)
  const [batchResult, setBatchResult] = useState<BatchLateSwapResponse | null>(null)
  const [batchError, setBatchError] = useState<string | null>(null)

  // Plan gate — set true when backend returns 403 (Pro required)
  const [planGated, setPlanGated] = useState(false)

  // Swap result comparisons: lineupIdx → comparison data
  const [swapComparisons, setSwapComparisons] = useState<Map<number, LineupSwapComparison>>(new Map())

  // Candidate pool: user-excluded player names (lowercase)
  const [poolExcludedPlayers, setPoolExcludedPlayers] = useState<Set<string>>(new Set())

  // FD pipeline: name→compositeId reverse lookup map (populated on FD import)
  const [fdNameToId, setFdNameToId] = useState<Map<string, string>>(new Map())

  // DK pipeline: name→playerId map (populated on DK client-side import)
  const [dkNameToId, setDkNameToId] = useState<Map<string, string>>(new Map())

  // Player status overrides
  const [playerConfirmed, setPlayerConfirmed] = useState<Set<string>>(new Set())
  const [injuryOpen, setInjuryOpen] = useState(false)

  // Hooks
  const { slates, selectedSlate, setSelectedId, slateFile, loading: slateLoading } = useLatestSlate()
  useEffect(() => {
    if (slateFile) {
      setFile(slateFile)
      setIsSlateOverride(false)
    }
  }, [slateFile])

  const { players: injuredPlayers } = useInjuryEvents()

  // Derived values
  const scratchedNames = scratchedText.split('\n').map(s => s.trim()).filter(Boolean)
  const scratchedNamesSet = new Set(scratchedNames.map(s => s.toLowerCase()))

  const lockedNames = lockedText.split('\n').map(s => s.trim()).filter(Boolean)
  const hasProjectionSlate = file != null
  const projectionSlateLabel = file?.name ?? selectedSlate?.file_name ?? null
  const projectionSlateSourceLabel = file
    ? (isSlateOverride ? 'override file' : 'saved slate')
    : null
  const readyForSwapGeneration = hasProjectionSlate
  const readyForEntryExport = managedLineups.length > 0
  const activeManagedLineup = managedLineups[activeLineupIdx] ?? null
  const hasManualLineup = lineupText.split('\n').map(s => s.trim()).filter(Boolean).length > 0
  const canRunSingleSwap = readyForSwapGeneration && !loading && (managedLineups.length > 0 ? Boolean(activeManagedLineup?.isAffected) : hasManualLineup)
  const canRunBatchSwap = readyForSwapGeneration && managedLineups.length > 0 && scratchedNames.length > 0 && !batchLoading
  const canExportEntries = managedLineups.length > 0
  const swapActionMessage = !readyForSwapGeneration
    ? 'Select a saved slate or override with a file to enable swap generation.'
    : managedLineups.length === 0
      ? hasManualLineup
        ? 'Single-lineup swap is ready.'
        : 'Import an entry CSV or enter a lineup manually to enable swaps.'
      : activeManagedLineup?.isAffected
        ? 'Active lineup has scratched players and is ready for swap generation.'
        : 'Active lineup has no scratched players. Mark scratches to enable swaps.'
  const batchActionMessage = !readyForSwapGeneration
    ? 'Select a saved slate or override with a file to enable auto-swap.'
    : managedLineups.length === 0
      ? 'Import an entry CSV to enable batch auto-swap and export.'
      : scratchedNames.length === 0
        ? 'Add scratched players to enable auto-swap.'
        : 'Batch auto-swap is ready.'
  const exportActionMessage = canExportEntries
    ? `Ready to export ${managedLineups.length} imported lineup${managedLineups.length === 1 ? '' : 's'}.`
    : 'Import an entry CSV to enable CSV export.'

  useEffect(() => {
    let cancelled = false

    const expectedPlatform = site === 'DK' ? 'draftkings' : 'fanduel'
    if (selectedSlate && selectedSlate.platform.toLowerCase() !== expectedPlatform) {
      setCompatibilityWarning(
        `Selected slate is ${selectedSlate.platform.toUpperCase()} while Late Swap is set to ${site}. Swap suggestions may be invalid until those match.`
      )
      return () => {
        cancelled = true
      }
    }

    if (!file || managedLineups.length === 0) {
      setCompatibilityWarning(null)
      return () => {
        cancelled = true
      }
    }

    const importedPlayers = [...new Set(
      managedLineups.flatMap(lineup => lineup.players.map(player => player.trim().toLowerCase()).filter(Boolean))
    )]

    if (importedPlayers.length === 0) {
      setCompatibilityWarning(null)
      return () => {
        cancelled = true
      }
    }

    void readFileAsText(file)
      .then(text => {
        if (cancelled) return
        const lines = text.split(/\r?\n/).filter(Boolean)
        if (lines.length === 0) {
          setCompatibilityWarning('Loaded projection slate could not be read. Swap suggestions may be unreliable until a valid slate is loaded.')
          return
        }

        const header = parseCSVRow(lines[0]).map(col => col.trim().toLowerCase())
        const nameIdx = header.findIndex(col => col === 'name' || col === 'nickname')
        if (nameIdx < 0) {
          setCompatibilityWarning('Loaded projection slate does not expose recognizable player-name columns. Swap suggestions may be unreliable until a standard slate file is loaded.')
          return
        }

        const slateNames = new Set<string>()
        for (const line of lines.slice(1)) {
          const row = parseCSVRow(line)
          const name = row[nameIdx]?.trim().toLowerCase()
          if (name) slateNames.add(name)
        }

        if (slateNames.size === 0) {
          setCompatibilityWarning('Loaded projection slate did not yield any player names. Swap suggestions may be unreliable until a valid slate is loaded.')
          return
        }

        const overlap = importedPlayers.filter(player => slateNames.has(player)).length
        const overlapRate = overlap / importedPlayers.length
        if (overlapRate < 0.65) {
          setCompatibilityWarning(
            `Imported entries only matched ${overlap} of ${importedPlayers.length} lineup players against the loaded projection slate. Swap results may be unreliable; export will still preserve imported entries.`
          )
          return
        }

        setCompatibilityWarning(null)
      })
      .catch(() => {
        if (!cancelled) {
          setCompatibilityWarning('Loaded projection slate could not be read. Swap suggestions may be unreliable until a valid slate is loaded.')
        }
      })

    return () => {
      cancelled = true
    }
  }, [file, managedLineups, selectedSlate, site])

  const injuryFeedPlayers = injuredPlayers.filter(p =>
    ['OUT', 'GTD', 'QUESTIONABLE', 'DOUBTFUL'].includes(p.status.toUpperCase())
  )

  const unconfirmedInjured = injuryFeedPlayers
    .filter(p => !playerConfirmed.has(p.player_name))
    .map(p => p.player_name)

  const allExcludedForReopt = [
    ...new Set([
      ...scratchedNames,
      ...unconfirmedInjured.filter(n => !scratchedNamesSet.has(n.toLowerCase())),
    ]),
  ]

  // Sync managed lineup affected flags when scratches change
  useEffect(() => {
    if (managedLineups.length === 0) return
    const nextManagedLineups = managedLineups.map(lu => ({
        ...lu,
        isAffected: lu.players.some(p => scratchedNamesSet.has(p.toLowerCase())),
      }))

    setManagedLineups(nextManagedLineups)

    const activeLineup = nextManagedLineups[activeLineupIdx]
    const preferredIndex = getPreferredActiveLineupIndex(nextManagedLineups, activeLineupIdx)
    if ((!activeLineup || !activeLineup.isAffected) && preferredIndex !== activeLineupIdx) {
      setActiveLineupIdx(preferredIndex)
      setResult(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scratchedText])

  // Sync active lineup to lineupText when switching
  useEffect(() => {
    if (managedLineups.length > 0 && managedLineups[activeLineupIdx]) {
      setLineupText(managedLineups[activeLineupIdx].players.join('\n'))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeLineupIdx])

  // -- Handlers ----------------------------------------------------------------

  const addScratched = (name: string) => {
    const existing = scratchedText.split('\n').map(s => s.trim()).filter(Boolean)
    if (!existing.map(s => s.toLowerCase()).includes(name.toLowerCase())) {
      setScratchedText([...existing, name].join('\n'))
    }
  }

  const toggleConfirmed = (name: string) => {
    setPlayerConfirmed(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const openInjuryReport = () => {
    setInjuryOpen(true)
    injuryRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const handleImportCSV = async (entryFile: File) => {
    // Entry templates are self-contained for import/export metadata, but they are
    // NOT valid projection-slate inputs for the backend swap/optimizer pipeline.
    setEntryTemplateFileName(entryFile.name)
    setImportLoading(true)
    setImportError(null)
    setParsedLineups(null)
    setManagedLineups([])
    try {
      // ------------------------------------------------------------------
      // Auto-detect site from CSV content and switch if needed
      // ------------------------------------------------------------------
      const peekText = await readFileAsText(entryFile)
      const detectedSite = detectCSVSite(peekText)
      if (detectedSite && detectedSite !== site) {
        setSite(detectedSite)
      }
      const effectiveSite = detectedSite ?? site

      // ------------------------------------------------------------------
      // FD path: self-contained import from entry upload template
      // ------------------------------------------------------------------
      if (effectiveSite === 'FD') {
        // Self-contained: parse player-pool (ID↔name) from the entry template itself.
        // This avoids the slate-ID mismatch bug where the server slate (e.g. "125392-")
        // differs from the entry template's slate (e.g. "127310-").
        const {
          lineups: fdEntries,
          warnings,
          nameToIdMap,
          lockedByGameTime,
        } = importFanDuelEntriesSelfContained(peekText)
        if (fdEntries.length === 0) {
          setImportError(
            warnings.length > 0
              ? warnings.slice(0, 3).join('; ')
              : 'No lineups found in the entry CSV — check that the file has data rows and use the FD entry upload template.'
          )
          return
        }

        // Build name→compositeId reverse map for batch-swap ID resolution later
        setFdNameToId(nameToIdMap)

        if (lockedByGameTime.length > 0) {
          const existingLocked = lockedText.split('\n').map(s => s.trim()).filter(Boolean)
          const existingLockedLower = new Set(existingLocked.map(s => s.toLowerCase()))
          const toAdd = lockedByGameTime.filter(n => !existingLockedLower.has(n.toLowerCase()))
          if (toAdd.length > 0) {
            setLockedText([...existingLocked, ...toAdd].join('\n'))
          }
        }

        const managed: ManagedLineup[] = fdEntries.map((entry, i) => ({
          id: i,
          label: `Lineup ${i + 1}`,
          players: entry.slots.map(s => s.playerName),
          originalPlayers: entry.slots.map(s => s.playerName),
          slotLabels: ['PG', 'PG', 'SG', 'SG', 'SF', 'SF', 'PF', 'PF', 'C'],
          isAffected: entry.slots.some(s => scratchedNamesSet.has(s.playerName.toLowerCase())),
          fdEntry: entry,
        }))
        const preferredIndex = getPreferredActiveLineupIndex(managed)
        setManagedLineups(managed)
        setActiveLineupIdx(preferredIndex)
        setLineupText(managed[preferredIndex].players.join('\n'))
        setParsedLineups(null)
        setSwapComparisons(new Map())
        setBatchResult(null)
        setPoolExcludedPlayers(new Set())
        setInjuryOpen(true)

        if (warnings.length > 0) {
          setImportError(
            `⚠ ${warnings.slice(0, 3).join('; ')}${warnings.length > 3 ? ` (+${warnings.length - 3} more)` : ''}`
          )
        }
        return
      }

      // ------------------------------------------------------------------
      // DK path: client-side import preserves Entry IDs and player IDs
      // ------------------------------------------------------------------
      const {
        lineups: dkEntries,
        warnings: dkWarnings,
        nameToIdMap,
        lockedByGameTime,
      } = importDraftKingsEntries(peekText)

      if (dkEntries.length === 0) {
        setImportError(
          dkWarnings.length > 0
            ? dkWarnings.slice(0, 3).join('; ')
            : 'No lineups found in the entry CSV — make sure you are using the DraftKings entries export file.',
        )
        return
      }

      setDkNameToId(nameToIdMap)

      // Auto-populate locked players from games that have already started
      if (lockedByGameTime.length > 0) {
        const existingLocked = lockedText.split('\n').map(s => s.trim()).filter(Boolean)
        const existingLockedLower = new Set(existingLocked.map(s => s.toLowerCase()))
        const toAdd = lockedByGameTime.filter(n => !existingLockedLower.has(n.toLowerCase()))
        if (toAdd.length > 0) {
          setLockedText([...existingLocked, ...toAdd].join('\n'))
        }
      }

      const managed: ManagedLineup[] = dkEntries.map((entry, i) => ({
        id: i,
        label: `Lineup ${i + 1}`,
        players: entry.slots.map(s => s.playerName),
        originalPlayers: entry.slots.map(s => s.playerName),
        slotLabels: entry.slots.map(s => s.slot),
        isAffected: entry.slots.some(s => scratchedNamesSet.has(s.playerName.toLowerCase())),
        dkEntry: entry,
      }))
      const preferredIndex = getPreferredActiveLineupIndex(managed)
      setManagedLineups(managed)
      setActiveLineupIdx(preferredIndex)
      setLineupText(managed[preferredIndex].players.join('\n'))
      setParsedLineups(null)
      setSwapComparisons(new Map())
      setBatchResult(null)
      setPoolExcludedPlayers(new Set())
      setInjuryOpen(true)

      if (dkWarnings.length > 0) {
        setImportError(
          `⚠ ${dkWarnings.slice(0, 3).join('; ')}${dkWarnings.length > 3 ? ` (+${dkWarnings.length - 3} more)` : ''}`,
        )
      }
      return
    } catch (err) {
      setImportError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setImportLoading(false)
    }
  }

  const applyParsedLineup = (lineup: ParsedLineup) => {
    setLineupText(lineup.players.join('\n'))
    setParsedLineups(null)
  }

  const handleFindSwaps = async () => {
    if (!file) { setError('Load a projection slate before running late swap. The entry template is only used for import/export.'); return }
    const lineup = lineupText.split('\n').map(s => s.trim()).filter(Boolean)
    const scratched = scratchedText.split('\n').map(s => s.trim()).filter(Boolean)
    const slotLabels = managedLineups.length > 0 ? (activeManagedLineup?.slotLabels ?? []) : undefined
    if (lineup.length === 0) { setError('Enter at least one lineup player'); return }
    if (scratched.length === 0) { setError('Enter at least one scratched player'); return }
    if (!weightsValid) { setError(`Scoring weights must sum to 1.0 (currently ${weightSum.toFixed(2)})`); return }

    setLoading(true)
    setError(null)
    setResult(null)
    setPlanGated(false)

    try {
      const res = await lateSwap(file, {
        site, lineup, scratched, slotLabels,
        lockedPlayers: lockedNames.length > 0 ? lockedNames : undefined,
        randomness: randomness / 100,
        n_candidates: nCandidates,
        sort_by: sortBy,
        w_proj: wProj,
        w_value: wValue,
        w_own: wOwn,
        contest_type: contestType === 'balanced' ? undefined : contestType,
      })
      if (!res.success || !res.data) {
        if (res.planGated) { setPlanGated(true); return }
        setError(res.error ?? 'Late swap failed')
        return
      }
      setResult({
        swaps: res.data.swaps,
        current_salary: res.data.current_salary,
        salary_cap: res.data.salary_cap,
        has_sim_data: res.data.has_sim_data,
        has_floor_data: res.data.has_floor_data ?? false,
        scoring_weights: res.data.scoring_weights,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  const handleUseCandidate = (scratchedPlayer: string, candidateName: string, dfsId?: string) => {
    // Duplicate guard — skip if the candidate is already in the active lineup
    // (but not as the scratched slot itself)
    const activePlayers = managedLineups.length > 0
      ? (managedLineups[activeLineupIdx]?.players ?? [])
      : lineupText.split('\n').map(s => s.trim()).filter(Boolean)
    const alreadyInLineup = activePlayers.some(
      p => p.toLowerCase() === candidateName.toLowerCase() &&
           p.toLowerCase() !== scratchedPlayer.toLowerCase()
    )
    if (alreadyInLineup) return

    // Record undo history before applying the swap
    if (managedLineups.length > 0 && managedLineups[activeLineupIdx]) {
      const prevManagedLineup = { ...managedLineups[activeLineupIdx], players: [...managedLineups[activeLineupIdx].players] }
      setSwapHistory(prev => [...prev, {
        lineupIdx: activeLineupIdx,
        removedPlayer: scratchedPlayer,
        addedPlayer: candidateName,
        prevLineupText: lineupText,
        prevManagedLineup,
        prevScratchedText: scratchedText,
      }])
    }

    setLineupText(
      lineupText.split('\n').map(l =>
        l.trim().toLowerCase() === scratchedPlayer.toLowerCase() ? candidateName : l
      ).join('\n')
    )
    if (managedLineups.length > 0) {
      const remainingScratches = scratchedNames.filter(s => s.toLowerCase() !== scratchedPlayer.toLowerCase())
      setManagedLineups(prev => prev.map((lu, i) => {
        if (i !== activeLineupIdx) return lu
        const players = lu.players.map(p =>
          p.toLowerCase() === scratchedPlayer.toLowerCase() ? candidateName : p
        )

        // Update FD slot entry — bind the new player ID to the correct slot
        let fdEntry = lu.fdEntry
        if (fdEntry && dfsId) {
          const slotIdx = fdEntry.slots.findIndex(
            s => s.playerName.toLowerCase() === scratchedPlayer.toLowerCase()
          )
          if (slotIdx >= 0) {
            // Extract slateId from the first existing composite ID in the lineup
            const existingComposite = fdEntry.slots.find(s => s.fdPlayerId.includes('-'))?.fdPlayerId ?? ''
            const slateId = existingComposite.split('-')[0] ?? ''
            const newFdPlayerId = slateId ? `${slateId}-${dfsId}` : dfsId
            fdEntry = {
              ...fdEntry,
              slots: fdEntry.slots.map((s, si) =>
                si === slotIdx
                  ? { ...s, fdPlayerId: newFdPlayerId, playerName: candidateName }
                  : s
              ),
            }
          }
        }

        // Update DK slot entry — bind the new player ID to the correct slot
        let dkEntry = lu.dkEntry
        if (dkEntry) {
          const slotIdx = dkEntry.slots.findIndex(
            s => s.playerName.toLowerCase() === scratchedPlayer.toLowerCase()
          )
          if (slotIdx >= 0) {
            const newPlayerId =
              dfsId ||
              dkNameToId.get(candidateName.toLowerCase()) ||
              dkNameToId.get(candidateName) ||
              ''
            dkEntry = {
              ...dkEntry,
              slots: dkEntry.slots.map((s, si) =>
                si === slotIdx
                  ? { ...s, playerId: newPlayerId, playerName: candidateName }
                  : s
              ),
            }
          }
        }

        return {
          ...lu,
          players,
          isAffected: players.some(p => remainingScratches.map(s => s.toLowerCase()).includes(p.toLowerCase())),
          fdEntry,
          dkEntry,
        }
      }))
    }
    // Track swap comparison
    if (result) {
      const swapResult = result.swaps.find(s => s.resolved_player.toLowerCase() === scratchedPlayer.toLowerCase())
      const candidate = swapResult?.candidates.find(c => c.name.toLowerCase() === candidateName.toLowerCase())
      if (swapResult && candidate) {
        setSwapComparisons(prev => {
          const next = new Map(prev)
          const existing = next.get(activeLineupIdx)
          const diff = {
            removed: swapResult.resolved_player,
            added: candidateName,
            position: swapResult.scratched_position,
            removedSalary: swapResult.scratched_salary,
            addedSalary: candidate.salary,
            projDelta: candidate.proj_delta,
            salaryDelta: candidate.salary_delta,
            success: true as const,
          }
          if (existing) {
            next.set(activeLineupIdx, {
              ...existing,
              diffs: [...existing.diffs, diff],
              finalSalary: candidate.new_total_salary,
              totalProjDelta: existing.totalProjDelta + candidate.proj_delta,
              totalSalaryDelta: existing.totalSalaryDelta + candidate.salary_delta,
              successCount: existing.successCount + 1,
            })
          } else {
            next.set(activeLineupIdx, {
              lineupIdx: activeLineupIdx,
              diffs: [diff],
              finalSalary: candidate.new_total_salary,
              originalSalary: result.current_salary,
              salaryCap: result.salary_cap,
              totalProjDelta: candidate.proj_delta,
              totalSalaryDelta: candidate.salary_delta,
              successCount: 1,
              failureCount: 0,
              source: 'manual',
              warnings: [],
            })
          }
          return next
        })
      }
    }
    setScratchedText(scratchedText.split('\n').filter(s => s.trim().toLowerCase() !== scratchedPlayer.toLowerCase()).join('\n'))
    setResult(null)
  }

  const handlePoolToggleExclude = (playerName: string) => {
    setPoolExcludedPlayers(prev => {
      const next = new Set(prev)
      const key = playerName.toLowerCase()
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const handleUseCandidateAll = (scratchedPlayer: string, candidateName: string, dfsId?: string) => {
    if (managedLineups.length === 0) { handleUseCandidate(scratchedPlayer, candidateName, dfsId); return }
    const remainingScratches = scratchedNames.filter(s => s.toLowerCase() !== scratchedPlayer.toLowerCase())
    // Identify which lineup indices contain the scratched player
    const affectedIndices: number[] = []
    setManagedLineups(prev => prev.map((lu, i) => {
      if (!lu.players.some(p => p.toLowerCase() === scratchedPlayer.toLowerCase())) return lu

      // Duplicate guard — skip lineups where candidate is already present (except as the scratched slot)
      const alreadyPresent = lu.players.some(
        p => p.toLowerCase() === candidateName.toLowerCase() &&
             p.toLowerCase() !== scratchedPlayer.toLowerCase()
      )
      if (alreadyPresent) return lu

      affectedIndices.push(i)
      const players = lu.players.map(p => p.toLowerCase() === scratchedPlayer.toLowerCase() ? candidateName : p)

      // Update FD slot entry
      let fdEntry = lu.fdEntry
      if (fdEntry && dfsId) {
        const slotIdx = fdEntry.slots.findIndex(
          s => s.playerName.toLowerCase() === scratchedPlayer.toLowerCase()
        )
        if (slotIdx >= 0) {
          const existingComposite = fdEntry.slots.find(s => s.fdPlayerId.includes('-'))?.fdPlayerId ?? ''
          const slateId = existingComposite.split('-')[0] ?? ''
          const newFdPlayerId = slateId ? `${slateId}-${dfsId}` : dfsId
          fdEntry = {
            ...fdEntry,
            slots: fdEntry.slots.map((s, si) =>
              si === slotIdx
                ? { ...s, fdPlayerId: newFdPlayerId, playerName: candidateName }
                : s
            ),
          }
        }
      }

      // Update DK slot entry
      let dkEntry = lu.dkEntry
      if (dkEntry) {
        const slotIdx = dkEntry.slots.findIndex(
          s => s.playerName.toLowerCase() === scratchedPlayer.toLowerCase()
        )
        if (slotIdx >= 0) {
          const newPlayerId =
            dfsId ||
            dkNameToId.get(candidateName.toLowerCase()) ||
            dkNameToId.get(candidateName) ||
            ''
          dkEntry = {
            ...dkEntry,
            slots: dkEntry.slots.map((s, si) =>
              si === slotIdx
                ? { ...s, playerId: newPlayerId, playerName: candidateName }
                : s
            ),
          }
        }
      }

      return {
        ...lu,
        players,
        isAffected: players.some(p => remainingScratches.map(s => s.toLowerCase()).includes(p.toLowerCase())),
        fdEntry,
        dkEntry,
      }
    }))
    const activePlayers = managedLineups[activeLineupIdx]?.players ?? []
    setLineupText(activePlayers.map(p => p.toLowerCase() === scratchedPlayer.toLowerCase() ? candidateName : p).join('\n'))
    setScratchedText(scratchedText.split('\n').filter(s => s.trim().toLowerCase() !== scratchedPlayer.toLowerCase()).join('\n'))
    // Track comparisons for all affected lineups
    if (result) {
      const swapResult = result.swaps.find(s => s.resolved_player.toLowerCase() === scratchedPlayer.toLowerCase())
      const candidate = swapResult?.candidates.find(c => c.name.toLowerCase() === candidateName.toLowerCase())
      if (swapResult && candidate) {
        setSwapComparisons(prev => {
          const next = new Map(prev)
          for (const idx of affectedIndices) {
            const existing = next.get(idx)
            const diff = {
              removed: swapResult.resolved_player,
              added: candidateName,
              position: swapResult.scratched_position,
              removedSalary: swapResult.scratched_salary,
              addedSalary: candidate.salary,
              projDelta: candidate.proj_delta,
              salaryDelta: candidate.salary_delta,
              success: true as const,
            }
            if (existing) {
              next.set(idx, {
                ...existing,
                diffs: [...existing.diffs, diff],
                finalSalary: candidate.new_total_salary,
                totalProjDelta: existing.totalProjDelta + candidate.proj_delta,
                totalSalaryDelta: existing.totalSalaryDelta + candidate.salary_delta,
                successCount: existing.successCount + 1,
              })
            } else {
              next.set(idx, {
                lineupIdx: idx,
                diffs: [diff],
                finalSalary: candidate.new_total_salary,
                originalSalary: result.current_salary,
                salaryCap: result.salary_cap,
                totalProjDelta: candidate.proj_delta,
                totalSalaryDelta: candidate.salary_delta,
                successCount: 1,
                failureCount: 0,
                source: 'manual',
                warnings: [],
              })
            }
          }
          return next
        })
      }
    }
    setResult(null)
  }

  const handleLineupTextChange = (text: string) => {
    setLineupText(text)
    if (managedLineups.length > 0) {
      setManagedLineups(prev => prev.map((lu, i) => {
        if (i !== activeLineupIdx) return lu
        const players = text.split('\n').map(l => l.trim()).filter(Boolean)
        return { ...lu, players, isAffected: players.some(p => scratchedNamesSet.has(p.toLowerCase())) }
      }))
    }
  }

  /**
   * One-click scratch from the lineup slot grid.
   * - Adds player to the scratched list
   * - Removes them from the locked list (a locked player can still be scratched
   *   if they get announced as OUT after their game started)
   */
  const handleScratchFromGrid = (playerName: string) => {
    // Add to scratched
    const existingScratched = scratchedText.split('\n').map(s => s.trim()).filter(Boolean)
    if (!existingScratched.map(s => s.toLowerCase()).includes(playerName.toLowerCase())) {
      setScratchedText([...existingScratched, playerName].join('\n'))
    }
    // Remove from locked if present
    const existingLocked = lockedText.split('\n').map(s => s.trim()).filter(Boolean)
    const newLocked = existingLocked.filter(s => s.toLowerCase() !== playerName.toLowerCase())
    if (newLocked.length !== existingLocked.length) {
      setLockedText(newLocked.join('\n'))
    }
    // Clear any existing swap result so it re-runs
    setResult(null)
  }

  const handleExportLineup = () => {
    const lines = lineupText.split('\n').map(s => s.trim()).filter(Boolean)
    const blob = new Blob([lines.join('\n')], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `lineup_${site}_${new Date().toISOString().slice(0, 10)}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleExportAllLineups = () => {
    if (managedLineups.length === 0) return
    // Pre-flight validation
    const validationErrors = validateExportReadiness(managedLineups, scratchedNamesSet)
    const blocking = validationErrors.filter(e => e.severity === 'error')
    if (blocking.length > 0) {
      setBatchError(`⚠ Export blocked — ${blocking.length} lineup${blocking.length > 1 ? 's' : ''} still contain scratched players: ${blocking.map(e => e.lineup).join(', ')}. Run swaps first or clear the scratch list.`)
      return
    }

    // FD: generate proper FanDuel upload CSV with entry IDs and composite player IDs
    if (site === 'FD') {
      const fdEntries = managedLineups
        .map(lu => lu.fdEntry)
        .filter((e): e is FDLineupEntry => e != null)
      if (fdEntries.length === 0) {
        setBatchError('⚠ No FanDuel entry data — re-import lineups with the FD site selected and a FD slate file loaded.')
        return
      }
      // FD-specific validation (duplicate IDs, empty slots…)
      const fdValidations = validateFDLineups(fdEntries)
      if (hasBlockingValidationErrors(fdValidations)) {
        setBatchError(`⚠ FD validation failed: ${blockingErrorSummary(fdValidations)}`)
        return
      }
      const csvContent = exportFanDuelLineups(fdEntries)
      downloadCSV(csvContent, `lineups_FD_late_swap_${new Date().toISOString().slice(0, 10)}.csv`)
      return
    }

    // DK: generate proper DraftKings upload CSV with Entry IDs and Name (ID) format
    if (site === 'DK') {
      const dkEntries = managedLineups
        .map(lu => lu.dkEntry)
        .filter((e): e is DKLineupEntry => e != null)
      if (dkEntries.length > 0) {
        const csvContent = exportDraftKingsLineups(dkEntries)
        downloadCSV(csvContent, `lineups_DK_late_swap_${new Date().toISOString().slice(0, 10)}.csv`)
        return
      }
    }

    // Fallback: plain-text export (no entry IDs available)
    const content = managedLineups
      .map(lu => `=== ${lu.label} ===\n${lu.players.join('\n')}`)
      .join('\n\n')
    const blob = new Blob([content], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `all_lineups_${site}_${new Date().toISOString().slice(0, 10)}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleReOptimize = async (params: {
    numLineups: number
    contestMode: ContestMode
    enableStacking: boolean
    minGameStack: number
    bringBackCount: number
  }) => {
    if (!file) {
      setReoptError('Load a projection slate before re-optimizing. Entry templates cannot drive the optimizer.')
      return
    }
    setReoptLoading(true)
    setReoptResult(null)
    setReoptError(null)
    try {
      const res = await runFullPipeline(file, {
        site,
        numLineups: params.numLineups,
        contestType: params.contestMode,
        enableStacking: params.enableStacking,
        minGameStack: params.minGameStack,
        bringBackCount: params.bringBackCount,
        outPlayers: allExcludedForReopt,
        lockedPlayers: lockedNames.length > 0 ? lockedNames : undefined,
        maxExposure: params.contestMode === 'cash' ? 0.8 : 0.6,
        refreshProps: true,
      })
      if (!res.success || !res.data) {
        setReoptError(res.error ?? 'Re-optimize failed')
        return
      }
      setReoptResult(res.data)
    } catch (err) {
      setReoptError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setReoptLoading(false)
    }
  }

  const handleDownloadReopt = async () => {
    if (!reoptResult?.download_file) return
    try {
      const res = await downloadLineups(reoptResult.download_file)
      if (!res.success || !res.data) return
      downloadFile(res.data, `lineups_${site}_late_swap_${new Date().toISOString().slice(0, 10)}.csv`)
    } catch { /* ignore */ }
  }

  const handleBatchAutoSwap = async () => {
    if (!file) {
      setBatchError('Load a projection slate before auto-swapping. Entry templates are only used for import/export.')
      return
    }
    if (managedLineups.length === 0) return
    setBatchLoading(true)
    setBatchResult(null)
    setBatchError(null)
    setPlanGated(false)
    try {
      const res = await batchLateSwap(file, {
        site,
        lineups: managedLineups.map(lu => lu.players),
        scratched: scratchedNames,
        lockedPlayers: lockedNames.length > 0 ? lockedNames : undefined,
        w_proj: wProj,
        w_value: wValue,
        w_own: wOwn,
        contest_type: contestType === 'balanced' ? undefined : contestType,
        diversity_factor: diversityFactor,
      })
      if (!res.success || !res.data) {
        if (res.planGated) { setPlanGated(true); return }
        setBatchError(res.error ?? 'Batch swap failed')
        return
      }
      setBatchResult(res.data)

      // Build swap comparisons and update managed lineups from batch log
      const newComparisons = new Map<number, LineupSwapComparison>()
      const updatedLineups = [...managedLineups]

      res.data.swap_log.forEach(log => {
        const zeroIdx = log.lineup_index - 1
        if (zeroIdx < 0 || zeroIdx >= managedLineups.length) return
        const lu = managedLineups[zeroIdx]

        const totalSalaryDelta = log.swaps.reduce((s, sw) => s + (sw.salary_delta ?? 0), 0)
        const totalProjDelta   = log.swaps.reduce((s, sw) => s + (sw.proj_delta   ?? 0), 0)
        const originalSalary   = log.total_salary - totalSalaryDelta

        const diffs = log.swaps.map(sw => ({
          removed: sw.scratched,
          added: sw.replacement ?? null,
          projDelta: sw.proj_delta ?? 0,
          salaryDelta: sw.salary_delta ?? 0,
          success: sw.replacement !== null,
          ...(sw.replacement === null ? {
            failureReason: FailureReason.NO_ELIGIBLE_REPLACEMENT,
            failureMessage: sw.note ?? `No replacement found for ${sw.scratched}`,
          } : {}),
        }))

        newComparisons.set(zeroIdx, {
          lineupIdx: zeroIdx,
          diffs,
          finalSalary: log.total_salary,
          originalSalary,
          salaryCap: log.salary_cap,
          totalProjDelta,
          totalSalaryDelta,
          successCount: log.swaps_made,
          failureCount: log.swaps_failed,
          source: 'batch',
          warnings: [],
        })

        // Update managed lineup's players to the post-swap roster
        if (log.swaps_made > 0) {
          let updatedPlayers = [...lu.players]
          let updatedFdEntry = lu.fdEntry ? { ...lu.fdEntry, slots: [...lu.fdEntry.slots] } : undefined
          let updatedDkEntry = lu.dkEntry ? { ...lu.dkEntry, slots: [...lu.dkEntry.slots] } : undefined

          for (const sw of log.swaps) {
            if (sw.replacement) {
              // Update players array
              updatedPlayers = updatedPlayers.map(p =>
                p.toLowerCase() === sw.scratched.toLowerCase() ? sw.replacement! : p
              )

              // Update FD slot entry — use fdNameToId map to find the new player's composite ID
              if (updatedFdEntry) {
                const slotIdx = updatedFdEntry.slots.findIndex(
                  s => s.playerName.toLowerCase() === sw.scratched.toLowerCase()
                )
                if (slotIdx >= 0) {
                  const replacementComposite =
                    fdNameToId.get(sw.replacement) ??
                    fdNameToId.get(sw.replacement.toLowerCase()) ??
                    ''
                  updatedFdEntry.slots = updatedFdEntry.slots.map((s, si) =>
                    si === slotIdx
                      ? { ...s, fdPlayerId: replacementComposite, playerName: sw.replacement! }
                      : s
                  )
                }
              }

              // Update DK slot entry — use dkNameToId map to find the new player's ID
              if (updatedDkEntry) {
                const slotIdx = updatedDkEntry.slots.findIndex(
                  s => s.playerName.toLowerCase() === sw.scratched.toLowerCase()
                )
                if (slotIdx >= 0) {
                  const replacementId =
                    dkNameToId.get(sw.replacement.toLowerCase()) ??
                    dkNameToId.get(sw.replacement) ??
                    ''
                  updatedDkEntry.slots = updatedDkEntry.slots.map((s, si) =>
                    si === slotIdx
                      ? { ...s, playerId: replacementId, playerName: sw.replacement! }
                      : s
                  )
                }
              }
            }
          }

          updatedLineups[zeroIdx] = {
            ...lu,
            players: updatedPlayers,
            isAffected: updatedPlayers.some(p => scratchedNamesSet.has(p.toLowerCase())),
            fdEntry: updatedFdEntry,
            dkEntry: updatedDkEntry,
          }
        }
      })

      setSwapComparisons(newComparisons)
      setManagedLineups(updatedLineups)
    } catch (err) {
      setBatchError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setBatchLoading(false)
    }
  }

  const handleDownloadBatch = async () => {
    if (!batchResult) return
    // Validation: block if scratched players remain after batch
    const validationErrors = validateExportReadiness(managedLineups, scratchedNamesSet)
    const blocking = validationErrors.filter(e => e.severity === 'error')
    if (blocking.length > 0) {
      setBatchError(`⚠ Export blocked — ${blocking.length} lineup${blocking.length > 1 ? 's' : ''} still contain scratched players. Run swaps for them first.`)
      return
    }

    // FD: generate proper FanDuel upload CSV client-side
    if (site === 'FD') {
      const fdEntries = managedLineups
        .map(lu => lu.fdEntry)
        .filter((e): e is FDLineupEntry => e != null)
      if (fdEntries.length === 0) {
        setBatchError('⚠ No FanDuel entry data available for export.')
        return
      }
      const fdValidations = validateFDLineups(fdEntries)
      if (hasBlockingValidationErrors(fdValidations)) {
        setBatchError(`⚠ FD validation failed: ${blockingErrorSummary(fdValidations)}`)
        return
      }
      const csvContent = exportFanDuelLineups(fdEntries)
      downloadCSV(csvContent, `lineups_FD_batch_swap_${new Date().toISOString().slice(0, 10)}.csv`)
      return
    }

    // DK: client-side export with preserved Entry IDs
    const dkEntries = managedLineups
      .map(lu => lu.dkEntry)
      .filter((e): e is DKLineupEntry => e != null)
    if (dkEntries.length > 0) {
      const csvContent = exportDraftKingsLineups(dkEntries)
      downloadCSV(csvContent, `lineups_DK_batch_swap_${new Date().toISOString().slice(0, 10)}.csv`)
      return
    }
    // Fall back to backend-generated file (no entry IDs — plain upload CSV)
    if (!batchResult.download_file) return
    try {
      const res = await downloadLineups(batchResult.download_file)
      if (!res.success || !res.data) return
      downloadFile(res.data, `lineups_${site}_batch_swap_${new Date().toISOString().slice(0, 10)}.csv`)
    } catch { /* ignore */ }
  }

  // Derived UI
  const isMultiLineup = managedLineups.length > 1
  const affectedCount = managedLineups.filter(lu => lu.isAffected).length

  return (
    <div className="bg-surface-base min-h-[calc(100vh-48px)] p-6">
      <div className="max-w-[1100px] mx-auto flex flex-col gap-4">

        {/* Header */}
        <div className="mb-5 flex justify-between items-start flex-wrap gap-3">
          <div>
            <h1 className="m-0 text-[22px] font-extrabold text-text-primary">Late Swap</h1>
            <p className="mt-1 mb-0 text-sm text-text-muted">
              Swap scratched players in submitted lineups, or rebuild fresh lineups with confirmed starters only.
            </p>
          </div>
          <div className="flex gap-2 flex-wrap items-center">
            {/* Mode tabs */}
            <div className="flex bg-surface-border rounded-lg p-[3px] gap-0.5">
              {([
                ['swap', 'Swap Existing', 'Find replacements for scratched players in your submitted lineups'],
                ['reoptimize', 'Re-Optimize', 'Build brand-new lineups with scratched players excluded'],
              ] as [Mode, string, string][]).map(([m, label, tip]) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  title={tip}
                  className={cn('px-4 py-[7px] rounded-md text-sm font-bold cursor-pointer border-none transition-all whitespace-nowrap',
                    mode === m ? (m === 'reoptimize' ? 'bg-[#059669] text-white' : 'bg-primary text-white') : 'bg-transparent text-text-muted'
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            {/* Site selector */}
            {(['DK', 'FD'] as Site[]).map(s => (
              <button
                key={s}
                onClick={() => { setSite(s); setResult(null); setReoptResult(null) }}
                className={cn('px-[18px] py-[7px] rounded-md text-sm font-bold cursor-pointer border-none',
                  site === s ? 'bg-[#0f766e] text-white' : 'bg-surface-border text-text-secondary'
                )}
              >
                {s}
              </button>
            ))}
            {/* Divider */}
            <div className="w-px h-6 bg-surface-border shrink-0" />
            {/* Import Entry CSV � always accessible in header */}
            <input
              ref={importRef}
              type="file"
              accept=".csv"
              className="hidden"
              onChange={e => { const f = e.target.files?.[0]; if (f) handleImportCSV(f); e.target.value = '' }}
            />
            <button
              onClick={() => importRef.current?.click()}
              disabled={importLoading}
              title="Import all lineups from your DK/FD entry CSV"
              className={cn(
                'px-3.5 py-[7px] rounded-md text-xs font-bold flex items-center gap-[5px] transition-colors',
                importLoading ? 'opacity-45 cursor-not-allowed' : 'cursor-pointer',
                managedLineups.length > 0 ? 'border border-[#166534] bg-[#052e16] text-success'
                  : 'border border-primary bg-primary/5 text-[#60a5fa]'
              )}
            >
              {importLoading ? '? Parsing�' : managedLineups.length > 0 ? `? ${managedLineups.length} Lineups` : '? Import Entry CSV'}
            </button>
          </div>
        </div>

        <div className="-mt-2 mb-1 flex flex-wrap gap-2">
          <span className={cn(
            'inline-flex items-center gap-1 rounded-full px-3 py-1 text-[11px] font-semibold border',
            entryTemplateFileName
              ? 'border-[#166534] bg-[#052e16] text-success'
              : 'border-surface-border bg-surface-overlay text-text-muted'
          )}>
            {entryTemplateFileName ? `Entry Template: ${entryTemplateFileName}` : 'Entry Template: not imported'}
          </span>
          <span className={cn(
            'inline-flex items-center gap-1 rounded-full px-3 py-1 text-[11px] font-semibold border',
            readyForSwapGeneration
              ? 'border-primary/30 bg-primary/5 text-[#60a5fa]'
              : 'border-danger/30 bg-danger/10 text-danger'
          )}>
            {projectionSlateLabel
              ? `Projection Slate (${projectionSlateSourceLabel}): ${projectionSlateLabel}`
              : 'Projection Slate: select a saved slate or override file'}
          </span>
          <span className={cn(
            'inline-flex items-center gap-1 rounded-full px-3 py-1 text-[11px] font-semibold border',
            readyForSwapGeneration && readyForEntryExport
              ? 'border-[#166534] bg-[#052e16] text-success'
              : 'border-surface-border bg-surface-overlay text-text-muted'
          )}>
            {readyForSwapGeneration && readyForEntryExport ? 'Ready: import, swap, export' : 'Ready state: incomplete'}
          </span>
        </div>

        {/* Slate selector */}
        <SlateSelector
          slates={slates}
          selected={selectedSlate}
          loading={slateLoading}
          onSelect={setSelectedId}
        />

        <div className="-mt-2 mb-1 flex flex-wrap items-center gap-2 text-[11px] text-text-muted">
          <span>Using the saved slate is the default late-swap flow.</span>
          {!showSlateOverridePicker ? (
            <button
              onClick={() => setShowSlateOverridePicker(true)}
              className="bg-transparent border-none p-0 text-[11px] font-semibold text-[#60a5fa] cursor-pointer underline"
            >
              Use a different projection file
            </button>
          ) : (
            <>
              <input
                ref={slateOverrideRef}
                type="file"
                accept=".csv"
                className="hidden"
                onChange={e => {
                  const f = e.target.files?.[0]
                  if (f) {
                    setFile(f)
                    setIsSlateOverride(true)
                    setResult(null)
                    setReoptResult(null)
                    setBatchResult(null)
                    setError(null)
                    setReoptError(null)
                    setBatchError(null)
                  }
                  e.target.value = ''
                }}
              />
              <button
                onClick={() => slateOverrideRef.current?.click()}
                className="px-2.5 py-1 rounded border border-surface-border bg-surface-overlay text-text-secondary text-[11px] font-semibold cursor-pointer hover:bg-surface-border transition-colors"
              >
                Choose override file
              </button>
              <button
                onClick={() => setShowSlateOverridePicker(false)}
                className="bg-transparent border-none p-0 text-[11px] font-semibold text-text-muted cursor-pointer underline"
              >
                Cancel
              </button>
            </>
          )}
        </div>

        {planGated && (
          <div className="bg-[#451a03] border border-[#f59e0b]/50 rounded-lg px-4 py-3 flex items-start gap-3">
            <span className="text-[#fbbf24] text-base mt-0.5">&#9888;</span>
            <div>
              <p className="m-0 text-sm font-bold text-[#fde68a]">Pro plan required</p>
              <p className="m-0 mt-1 text-xs text-[#fde68a]/80">The Late Swap engine is a Pro feature. Upgrade to unlock single and batch swap operations.</p>
              <a href="/billing" className="inline-block mt-2 px-3 py-1.5 rounded text-xs font-bold bg-[#f59e0b] text-[#1c1917] no-underline hover:bg-[#fbbf24] transition-colors">
                Upgrade to Pro &rarr;
              </a>
            </div>
          </div>
        )}

        {!hasProjectionSlate && (
          <div className="bg-[#450a0a] border border-danger/40 rounded-lg px-4 py-3 text-sm text-[#fca5a5]">
            Select a saved projection slate before using Find Best Swaps, Auto-Swap, or Re-Optimize. The imported DK/FD entry template is only used to preserve entry IDs, exact slot positions, and export format.
          </div>
        )}

        {importError && (
          <div className="bg-[#450a0a] border border-danger rounded-lg px-4 py-3 text-sm text-[#fca5a5]">
            {importError}
          </div>
        )}

        {compatibilityWarning && (
          <div className="bg-[#3b2500] border border-[#f59e0b]/40 rounded-lg px-4 py-3 text-sm text-[#fde68a]">
            {compatibilityWarning}
          </div>
        )}

        {mode === 'swap' && (
          <div className="bg-surface-overlay border border-surface-border rounded-xl px-4 py-3 flex flex-col gap-3">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div>
                <div className="text-xs font-bold text-text-secondary uppercase tracking-[0.05em]">Swap Actions</div>
                <div className="text-[11px] text-text-muted mt-1">
                  Import entries for bulk export. Load a projection slate for swap generation.
                </div>
              </div>
              <div className="flex gap-2 flex-wrap">
                <button
                  onClick={() => importRef.current?.click()}
                  disabled={importLoading}
                  className={cn(
                    'px-3 py-2 rounded-lg text-xs font-bold transition-colors',
                    importLoading ? 'bg-surface-border opacity-50 cursor-not-allowed' : 'bg-primary/10 text-[#60a5fa] border border-primary/30 cursor-pointer hover:bg-primary/15'
                  )}
                >
                  {importLoading ? 'Importing...' : managedLineups.length > 0 ? `Imported ${managedLineups.length}` : 'Import Entry CSV'}
                </button>
                <button
                  onClick={handleFindSwaps}
                  disabled={!canRunSingleSwap}
                  className={cn(
                    'px-3 py-2 rounded-lg text-xs font-bold transition-colors',
                    canRunSingleSwap ? 'bg-primary text-white cursor-pointer hover:bg-primary-hover' : 'bg-surface-border text-text-muted opacity-60 cursor-not-allowed'
                  )}
                >
                  Find Best Swaps
                </button>
                <button
                  onClick={openInjuryReport}
                  disabled={injuryFeedPlayers.length === 0}
                  className={cn(
                    'px-3 py-2 rounded-lg text-xs font-bold transition-colors',
                    injuryFeedPlayers.length > 0 ? 'bg-[#78350f]/20 text-[#fbbf24] border border-[#fbbf24]/30 cursor-pointer hover:bg-[#78350f]/30' : 'bg-surface-border text-text-muted opacity-60 cursor-not-allowed'
                  )}
                >
                  Open Injury Report
                </button>
                <button
                  onClick={handleBatchAutoSwap}
                  disabled={!canRunBatchSwap}
                  className={cn(
                    'px-3 py-2 rounded-lg text-xs font-bold transition-colors',
                    canRunBatchSwap ? 'bg-[#059669] text-white cursor-pointer hover:bg-[#047857]' : 'bg-surface-border text-text-muted opacity-60 cursor-not-allowed'
                  )}
                >
                  Swap Affected Lineups
                </button>
                <button
                  onClick={handleExportAllLineups}
                  disabled={!canExportEntries}
                  className={cn(
                    'px-3 py-2 rounded-lg text-xs font-bold transition-colors',
                    canExportEntries ? 'bg-[#166534] text-white cursor-pointer hover:bg-[#14532d]' : 'bg-surface-border text-text-muted opacity-60 cursor-not-allowed'
                  )}
                >
                  Export All
                </button>
              </div>
            </div>
            <div className="grid gap-2 md:grid-cols-3 text-[11px]">
              <div className="rounded-lg border border-surface-border bg-surface-base px-3 py-2 text-text-muted">
                <span className="font-semibold text-text-primary">Swap:</span> {swapActionMessage}
              </div>
              <div className="rounded-lg border border-surface-border bg-surface-base px-3 py-2 text-text-muted">
                <span className="font-semibold text-text-primary">Auto-Swap:</span> {batchActionMessage}
              </div>
              <div className="rounded-lg border border-surface-border bg-surface-base px-3 py-2 text-text-muted">
                <span className="font-semibold text-text-primary">Export:</span> {exportActionMessage}
              </div>
            </div>
            {managedLineups.length > 0 && scratchedNames.length === 0 && (
              <div className="rounded-lg border border-[#fbbf24]/25 bg-[#1a1200] px-3 py-2 text-[11px] text-[#fde68a] flex items-center justify-between gap-3 flex-wrap">
                <span>
                  No lineups are marked for swap yet. Open the Injury Report or use the per-player Scratch buttons in the lineup card.
                </span>
                <button
                  onClick={openInjuryReport}
                  disabled={injuryFeedPlayers.length === 0}
                  className={cn(
                    'px-2.5 py-1 rounded-md text-[11px] font-bold transition-colors',
                    injuryFeedPlayers.length > 0 ? 'bg-[#78350f] text-[#fde68a] cursor-pointer hover:bg-[#92400e]' : 'bg-surface-border text-text-muted opacity-60 cursor-not-allowed'
                  )}
                >
                  Open Injury Report
                </button>
              </div>
            )}
          </div>
        )}

        {/* Injury Report � Confirm or Scratch (collapsible) */}
        {injuryFeedPlayers.length > 0 && (() => {
          const outCount = injuryFeedPlayers.filter(p => p.status.toUpperCase() === 'OUT').length
          const gtdCount = injuryFeedPlayers.filter(p => !['OUT'].includes(p.status.toUpperCase())).length
          const scratchedCount = injuryFeedPlayers.filter(p => scratchedNamesSet.has(p.player_name.toLowerCase())).length
          const confirmedCount = injuryFeedPlayers.filter(p => playerConfirmed.has(p.player_name)).length
          return (
            <div ref={injuryRef} className="bg-surface-overlay border border-amber-900/30 rounded-xl overflow-hidden">
              {/* -- Header / toggle -- */}
              <button
                onClick={() => setInjuryOpen(o => !o)}
                className="w-full flex items-center justify-between px-[18px] py-3 bg-transparent border-none cursor-pointer text-left gap-3"
              >
                <div className="flex items-center gap-2.5 flex-wrap">
                  <span className="text-xs font-bold text-[#fbbf24] uppercase tracking-[0.05em]">
                    Injury Report � Confirm Player Status
                  </span>
                  <span className="text-[11px] bg-[#7f1d1d] text-[#fca5a5] px-2 py-px rounded-full font-bold">
                    {outCount} OUT
                  </span>
                  {gtdCount > 0 && (
                    <span className="text-[11px] bg-[#78350f] text-[#fde68a] px-2 py-px rounded-full font-bold">
                      {gtdCount} GTD/Q
                    </span>
                  )}
                  {scratchedCount > 0 && (
                    <span className="text-[11px] bg-[#450a0a] text-[#f87171] px-2 py-px rounded-full font-bold">
                      {scratchedCount} scratched
                    </span>
                  )}
                  {confirmedCount > 0 && (
                    <span className="text-[11px] bg-success-muted text-success px-2 py-px rounded-full font-bold">
                      {confirmedCount} confirmed
                    </span>
                  )}
                  {allExcludedForReopt.length > 0 && (
                    <span className="text-[11px] text-[#f97316]">
                      {allExcludedForReopt.length} excluded from Re-Opt
                    </span>
                  )}
                </div>
                <span className={cn('text-base text-[#fbbf24] shrink-0 inline-block transition-transform duration-200', injuryOpen && 'rotate-180')}>
                  ?
                </span>
              </button>

              {/* -- Collapsible body -- */}
              {injuryOpen && (
                <div className="px-[18px] pb-3.5">
                  {/* Quick actions strip */}
                  <div className="flex gap-[7px] flex-wrap mb-3 pb-3 border-b border-surface-border">
                    <button
                      onClick={() => {
                        const outPlayers = injuryFeedPlayers.filter(p => p.status.toUpperCase() === 'OUT').map(p => p.player_name)
                        const existing = scratchedText.split('\n').map(s => s.trim()).filter(Boolean)
                        const existingLower = new Set(existing.map(s => s.toLowerCase()))
                        const toAdd = outPlayers.filter(n => !existingLower.has(n.toLowerCase()))
                        if (toAdd.length > 0) setScratchedText([...existing, ...toAdd].join('\n'))
                      }}
                      className="px-2.5 py-1 rounded border border-[#7f1d1d] text-[11px] font-bold cursor-pointer bg-[#1f0a0a] text-[#fca5a5]"
                    >
                      ? Scratch All OUT ({injuryFeedPlayers.filter(p => p.status.toUpperCase() === 'OUT').length})
                    </button>
                    <button
                      onClick={() => {
                        const gtdPlayers = injuryFeedPlayers.filter(p => p.status.toUpperCase() !== 'OUT' && !playerConfirmed.has(p.player_name)).map(p => p.player_name)
                        const existing = scratchedText.split('\n').map(s => s.trim()).filter(Boolean)
                        const existingLower = new Set(existing.map(s => s.toLowerCase()))
                        const toAdd = gtdPlayers.filter(n => !existingLower.has(n.toLowerCase()))
                        if (toAdd.length > 0) setScratchedText([...existing, ...toAdd].join('\n'))
                      }}
                      className="px-2.5 py-1 rounded border border-[#78350f] text-[11px] font-bold cursor-pointer bg-[#1a1200] text-[#fde68a]"
                    >
                      ? Scratch All GTD ({injuryFeedPlayers.filter(p => p.status.toUpperCase() !== 'OUT').length})
                    </button>
                    <button
                      onClick={() => {
                        setPlayerConfirmed(prev => {
                          const next = new Set(prev)
                          injuryFeedPlayers.filter(p => p.status.toUpperCase() !== 'OUT').forEach(p => next.add(p.player_name))
                          return next
                        })
                      }}
                      className="px-2.5 py-1 rounded border border-success/30 text-[11px] font-bold cursor-pointer bg-success-muted text-success"
                    >
                      ? Confirm All GTD Active
                    </button>
                    {scratchedNames.length > 0 && (
                      <button
                        onClick={() => setScratchedText('')}
                        className="px-2.5 py-1 rounded border border-surface-border text-[11px] font-semibold cursor-pointer bg-transparent text-text-muted ml-auto"
                      >
                        Clear {scratchedNames.length} scratched
                      </button>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-2 mb-2.5">
                    {injuryFeedPlayers.map(p => {
                      const isOut = p.status.toUpperCase() === 'OUT'
                      const alreadyAdded = scratchedNamesSet.has(p.player_name.toLowerCase())
                      const isConfirmed = playerConfirmed.has(p.player_name)
                      return (
                        <div
                          key={p.player_name}
                          className={cn('flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs',
                            isConfirmed ? 'bg-[#0d1f17] border border-success/20'
                              : isOut ? 'bg-[#1f0a0a] border border-danger/20'
                              : 'bg-[#1a1200] border border-[#fbbf24]/20'
                          )}
                        >
                          <div>
                            <span className={cn("font-bold", isConfirmed ? "text-success" : "text-text-primary")}>
                              {p.player_name}
                            </span>
                            {p.team && (
                              <span className="text-[10px] text-text-muted ml-1">{p.team}</span>
                            )}
                          </div>
                          <span className={cn("text-[10px] font-bold px-1.5 py-px rounded", isOut ? "bg-[#7f1d1d] text-[#fca5a5]" : "bg-[#78350f] text-[#fde68a]")}>
                            {p.status}
                          </span>
                          <button
                            onClick={() => toggleConfirmed(p.player_name)}
                            title="Confirmed to play � eligible as a swap candidate"
                            className={cn('px-2 py-[3px] rounded border text-[11px] font-bold cursor-pointer transition-colors',
                              isConfirmed ? 'border-success/30 bg-success-muted text-success' : 'border-surface-border bg-surface-border text-text-muted'
                            )}
                          >
                            {isConfirmed ? 'Active' : 'Confirm Active'}
                          </button>
                          <button
                            onClick={() => {
                              if (!alreadyAdded) addScratched(p.player_name)
                              if (isConfirmed) toggleConfirmed(p.player_name)
                            }}
                            disabled={alreadyAdded}
                            title="Add to scratched list"
                            className={cn('px-2 py-[3px] rounded border text-[11px] font-bold transition-colors',
                              alreadyAdded ? 'border-danger/20 bg-[#2d0a0a] text-[#fca5a5] opacity-70 cursor-default' : 'border-surface-border bg-surface-border text-text-muted cursor-pointer'
                            )}
                          >
                            {alreadyAdded ? 'Scratched' : 'Scratch'}
                          </button>
                        </div>
                      )
                    })}
                  </div>
                  <div className="text-[11px] text-text-muted">
                    Mark each player as
                    {' '}<strong className="text-success">Confirm Active</strong> (confirmed playing � eligible for swap candidates) or
                    {' '}<strong className="text-danger">Scratch</strong> (OUT � adds to scratch list and excluded from Re-Optimize).
                  </div>
                </div>
              )}
            </div>
          )
        })()}

        {/* --- Locked Players (Started Games) --- */}
        <div className="bg-surface-overlay border border-sky-500/20 rounded-xl px-[18px] py-3.5">
          <div className={cn('flex justify-between items-center', lockedText.trim() ? 'mb-2.5' : '')}>
            <div className="text-xs font-bold text-[#38bdf8] uppercase tracking-[0.05em] flex items-center gap-2">
              ?? Locked Players � Started Games
              {lockedNames.length > 0 && (
                <span className="text-[11px] font-bold px-2 py-px rounded-full bg-[#0c4a6e] text-[#38bdf8]">
                  {lockedNames.length} locked
                </span>
              )}
            </div>
            <button
              onClick={() => setLockedText("")}
              className="text-[11px] text-text-muted bg-transparent border-none cursor-pointer px-1.5 py-0.5"
              title="Clear locked players"
            >
              Clear
            </button>
          </div>
          <textarea
            value={lockedText}
            onChange={e => setLockedText(e.target.value)}
            placeholder={'Enter player names � one per line\nThese players are in games that have ALREADY STARTED.\nThey will be kept in your lineups and cannot be used as swap candidates.'}
            rows={lockedText.trim() ? Math.min(8, lockedText.split('\n').length + 1) : 3}
className={cn(inpCls, 'resize-y leading-relaxed font-mono text-xs')}
          />
          <div className="mt-1.5 text-[11px] text-text-muted">
            Players listed here will be <strong className="text-[#38bdf8]">force-kept</strong> in every lineup regardless of the scratched list,
            and will <strong className="text-[#38bdf8]">not appear</strong> as replacement candidates.
            Use this for players whose game has already tipped off.
          </div>
        </div>

        {/* ------------------- MODE: RE-OPTIMIZE ------------------- */}
        {mode === 'reoptimize' && (
          <ReOptimizePanel
            file={file}
            site={site}
            excludedPlayers={allExcludedForReopt}
            loading={reoptLoading}
            result={reoptResult}
            error={reoptError}
            onRun={handleReOptimize}
            onDownload={handleDownloadReopt}
          />
        )}

        {/* ------------------- MODE: SWAP EXISTING ------------------- */}
        {mode === 'swap' && (
          <>
            {/* ── Scoring Config Panel (Phase 1.1 / 1.2 / 1.4) ── */}
            <div className="bg-surface-overlay border border-surface-border rounded-xl px-[18px] py-3.5">
              <div className="flex flex-wrap gap-x-6 gap-y-3 items-start">
                {/* Contest type toggle */}
                <div className="flex flex-col gap-1.5">
                  <span className="text-[11px] font-bold text-text-muted uppercase tracking-[0.05em]">Contest Type</span>
                  <div className="flex gap-1">
                    {(['cash', 'balanced', 'gpp'] as ContestType[]).map(ct => (
                      <button
                        key={ct}
                        onClick={() => applyPreset(ct)}
                        className={cn(
                          'px-3 py-1 rounded text-[11px] font-bold border transition-colors cursor-pointer',
                          contestType === ct
                            ? ct === 'cash' ? 'bg-[#064e3b] border-success text-success'
                              : ct === 'gpp' ? 'bg-[#312e81] border-[#818cf8] text-[#818cf8]'
                              : 'bg-[#0c3f6e] border-[#38bdf8] text-[#38bdf8]'
                            : 'bg-surface-border border-surface-border text-text-muted hover:text-text-secondary'
                        )}
                      >
                        {ct === 'cash' ? 'Cash' : ct === 'balanced' ? 'Balanced' : 'GPP'}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Scoring weight sliders */}
                <div className="flex flex-col gap-1.5 min-w-[220px]">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-bold text-text-muted uppercase tracking-[0.05em]">Scoring Weights</span>
                    <span className={cn('text-[10px] font-bold px-1.5 py-px rounded', weightsValid ? 'text-success bg-success-muted' : 'text-danger bg-[#450a0a]')}>
                      {(wProj + wValue + wOwn).toFixed(2)} {weightsValid ? '✓' : '≠ 1.0'}
                    </span>
                  </div>
                  {([
                    { label: 'Proj', value: wProj, set: setWProj, color: '#22d3ee' },
                    { label: 'Value', value: wValue, set: setWValue, color: '#a78bfa' },
                    { label: 'Own% ↓', value: wOwn, set: setWOwn, color: '#f97316' },
                  ] as { label: string; value: number; set: (v: number) => void; color: string }[]).map(({ label, value, set, color }) => (
                    <div key={label} className="flex items-center gap-2">
                      <span className="text-[10px] w-[38px] text-right" style={{ color }}>{label}</span>
                      <input
                        type="range" min={0} max={1} step={0.05}
                        value={value}
                        onChange={e => { set(Number(e.target.value)) }}
                        style={{ accentColor: color, width: 100, cursor: 'pointer' }}
                      />
                      <span className="text-[10px] text-text-muted w-[28px]">{Math.round(value * 100)}%</span>
                    </div>
                  ))}
                </div>

                {/* Diversity slider (batch mode) */}
                {managedLineups.length > 1 && (
                  <div className="flex flex-col gap-1.5">
                    <span className="text-[11px] font-bold text-text-muted uppercase tracking-[0.05em]">Diversity (Batch)</span>
                    <div className="flex items-center gap-2">
                      <input
                        type="range" min={0} max={1} step={0.1}
                        value={diversityFactor}
                        onChange={e => setDiversityFactor(Number(e.target.value))}
                        style={{ accentColor: '#f59e0b', width: 100, cursor: 'pointer' }}
                      />
                      <span className={cn('text-[10px] font-bold min-w-[30px]', diversityFactor > 0 ? 'text-[#f59e0b]' : 'text-text-muted')}>
                        {Math.round(diversityFactor * 100)}%
                      </span>
                    </div>
                    <span className="text-[10px] text-text-muted max-w-[160px]">Penalises re-using the same replacement across lineups</span>
                  </div>
                )}

                {/* Undo / Reset controls */}
                <div className="flex flex-col gap-1.5 ml-auto justify-end">
                  <span className="text-[11px] font-bold text-text-muted uppercase tracking-[0.05em]">History</span>
                  <div className="flex gap-1.5">
                    <button
                      onClick={handleUndo}
                      disabled={swapHistory.length === 0}
                      title={swapHistory.length > 0 ? `Undo: revert ${swapHistory[swapHistory.length - 1]?.addedPlayer} → ${swapHistory[swapHistory.length - 1]?.removedPlayer}` : 'Nothing to undo'}
                      className={cn(
                        'px-3 py-1 rounded text-[11px] font-bold border transition-colors',
                        swapHistory.length > 0 ? 'border-[#5b21b6] bg-transparent text-[#a78bfa] cursor-pointer hover:bg-[#5b21b6]/10' : 'border-surface-border text-text-muted opacity-50 cursor-not-allowed'
                      )}
                    >
                      ↩ Undo{swapHistory.length > 0 ? ` (${swapHistory.length})` : ''}
                    </button>
                    <button
                      onClick={handleResetToOriginal}
                      disabled={managedLineups.length === 0 && !lineupText.trim()}
                      title="Restore all lineups to original imported players"
                      className={cn(
                        'px-3 py-1 rounded text-[11px] font-bold border transition-colors',
                        (managedLineups.length > 0 || lineupText.trim()) ? 'border-danger/40 bg-transparent text-danger cursor-pointer hover:bg-[#7f1d1d]/20' : 'border-surface-border text-text-muted opacity-50 cursor-not-allowed'
                      )}
                    >
                      ⟳ Reset All
                    </button>
                  </div>
                </div>
              </div>
            </div>

            {managedLineups.length > 0 ? (
              /* --- 2-panel layout when lineups are loaded --- */
              <div className="flex gap-4 items-start">

                {/* -- LEFT: Compact Lineup Table -- */}
                <div className="w-[268px] shrink-0 bg-surface-overlay border border-surface-border rounded-xl overflow-hidden flex flex-col sticky top-4 max-h-[calc(100vh-100px)]">
                  {/* Table header */}
                  <div className="px-3.5 py-[11px] border-b border-surface-border bg-surface-base flex justify-between items-center">
                    <span className="text-sm font-bold text-text-primary">{managedLineups.length} Lineups</span>
                    <div className="flex gap-[5px]">
                      {affectedCount > 0
                        ? <span className="text-[10px] bg-[#450a0a] text-[#fca5a5] px-[7px] py-0.5 rounded-lg font-bold">{affectedCount} affected</span>
                        : <span className="text-[10px] bg-success-muted text-success px-[7px] py-0.5 rounded-lg font-bold">? all clean</span>
                      }
                    </div>
                  </div>

                  {/* Scratched summary */}
                  {scratchedNames.length > 0 ? (
                    <div className="px-3.5 py-2 border-b border-surface-border bg-[#1a0c0c]">
                      <div className="flex justify-between items-center mb-[5px]">
                        <span className="text-[10px] font-bold text-danger uppercase">{scratchedNames.length} Scratched</span>
                        <button onClick={() => setScratchedText("")} className="text-[10px] text-text-muted bg-transparent border-none cursor-pointer px-1 py-px">clear</button>
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {scratchedNames.map(n => (
                          <span key={n} className="text-[10px] bg-[#2d0a0a] text-[#fca5a5] px-1.5 py-0.5 rounded font-semibold">{n}</span>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="px-3.5 py-[7px] border-b border-surface-border bg-[#0f1a0f]">
                      <span className="text-[11px] text-text-muted">
                        No scratched players � use <strong className="text-warning">Injury Report</strong> above
                      </span>
                    </div>
                  )}

                  {/* Lineup rows */}
                  <div className="overflow-y-auto flex-1">
                    {managedLineups.map((lu, i) => (
                      <div
                        key={i}
                        onClick={() => { setActiveLineupIdx(i); setResult(null) }}
                        className="px-3.5 py-[9px] cursor-pointer border-b border-[#1e293b22] transition-colors"
                        style={{
                          borderLeft: `3px solid ${i === activeLineupIdx ? '#3b82f6' : lu.isAffected ? '#ef4444' : 'transparent'}`,
                          background: i === activeLineupIdx ? '#1e3a5f22' : 'transparent',
                        }}
                      >
                        <div className="flex justify-between items-center mb-[3px]">
                          <span className={cn("text-xs font-bold", i === activeLineupIdx ? "text-[#60a5fa]" : "text-text-primary")}>{lu.label}</span>
                          {lu.isAffected
                            ? <span className="text-[10px] text-danger font-bold">?</span>
                            : <span className="text-[10px] text-success">?</span>
                          }
                        </div>
                        <div className="text-[11px] text-text-muted overflow-hidden text-ellipsis whitespace-nowrap">
                          {lu.players.slice(0, 3).join(', ')}{lu.players.length > 3 ? '�' : ''}
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* Footer: bulk actions */}
                  <div className="px-3.5 py-2.5 border-t border-surface-border bg-surface-base flex flex-col gap-[7px]">
                    <button
                      onClick={handleBatchAutoSwap}
                      disabled={batchLoading || scratchedNames.length === 0}
                      className={cn('px-2.5 py-2 rounded border-none text-xs font-bold w-full text-white transition-colors',
                        batchLoading || !scratchedNames.length ? 'bg-surface-border opacity-50 cursor-not-allowed' : 'bg-[#059669] hover:bg-[#047857] cursor-pointer'
                      )}
                    >
                      {batchLoading ? '? Swapping�' : '? Swap Affected Lineups'}
                    </button>
                    <div className="flex gap-1.5">
                      <button onClick={handleExportAllLineups} className={cn(secBtnCls, "text-[11px] px-2 py-[5px] flex-1")}>
                        Export All ({managedLineups.length})
                      </button>
                      <button
                        onClick={() => { setManagedLineups([]); setLineupText(''); setResult(null); setBatchResult(null) }}
                        className={cn(secBtnCls, "text-[11px] px-2 py-[5px] flex-1")}
                      >
                        Clear
                      </button>
                    </div>
                  </div>
                </div>

                {/* -- RIGHT: Selected lineup visual + results -- */}
                <div className="flex-1 min-w-0 flex flex-col gap-3">
                  {managedLineups[activeLineupIdx] && (() => {
                    const activeLu = managedLineups[activeLineupIdx]
                    const scratchCountInLu = activeLu.players.filter(p => scratchedNamesSet.has(p.toLowerCase())).length
                    return (
                      <>
                        {/* Visual lineup card */}
                        <div className={cardCls}>
                          <div className="flex justify-between items-start mb-3.5 flex-wrap gap-2">
                            <div>
                              <div className="text-[15px] font-extrabold text-text-primary mb-0.5">{activeLu.label}</div>
                              {activeLu.isAffected
                                ? <div className="text-xs text-danger">? {scratchCountInLu} scratched � find swaps below</div>
                                : <div className="text-xs text-success">? All players active</div>
                              }
                            </div>
                            <div className="flex gap-2 items-center flex-wrap">
                              <div className="flex items-center gap-1.5">
                                <span className="text-[11px] text-text-muted">Variance</span>
                                <input type="range" min={0} max={100} step={5} value={randomness} onChange={e => { setRandomness(Number(e.target.value)); setResult(null) }} style={{ width: 70, accentColor: '#a78bfa', cursor: 'pointer' }} />
                                <span className={cn("text-[11px] font-bold min-w-[28px]", randomness === 0 ? "text-text-muted" : "text-[#a78bfa]")}>{randomness}%</span>
                              </div>
                              <select value={sortBy} onChange={e => setSortBy(e.target.value as typeof sortBy)} className="bg-surface-border text-text-secondary border border-surface-border rounded px-2 py-1 text-[11px] cursor-pointer outline-none">
                                <option value="swap_score">Score</option>
                                <option value="projection">Proj</option>
                                <option value="ceiling">Ceiling</option>
                                <option value="value">Value</option>
                                <option value="own">Own% ?</option>
                                <option value="floor">Floor</option>
                              </select>
                              <button
                                onClick={handleFindSwaps}
                                disabled={!file || loading || !activeLu.isAffected}
                                className={cn('px-5 py-2 rounded-lg border-none text-sm font-bold text-white transition-colors',
                                  !file || loading || !activeLu.isAffected ? 'bg-surface-border opacity-50 cursor-not-allowed' : 'bg-primary hover:bg-primary-hover cursor-pointer'
                                )}
                              >
                                {loading ? 'Finding�' : activeLu.isAffected ? 'Find Best Swaps' : 'Mark Scratches First'}
                              </button>
                              {randomness > 0 && result && (
                                <button onClick={handleFindSwaps} disabled={loading} className="px-3.5 py-2 rounded-lg border border-[#5b21b6] text-xs font-bold cursor-pointer bg-transparent text-[#a78bfa] hover:bg-[#5b21b6]/10 transition-colors">Re-roll</button>
                              )}
                            </div>
                          </div>

                          {/* Slot grid — one-click scratch per player */}
                          <LineupSlotGrid
                            lineup={activeLu}
                            site={site}
                            scratchedNamesSet={scratchedNamesSet}
                            lockedNames={lockedNames}
                            onScratch={handleScratchFromGrid}
                          />
                        </div>

                        {/* Error */}
                        {error && (
                          <div className="bg-[#450a0a] border border-danger rounded-lg px-4 py-2.5 text-[#fca5a5] text-sm">{error}</div>
                        )}

                        {/* Swap results */}
                        {result && (
                          <>
                            <div className="flex gap-3 flex-wrap px-3.5 py-2 bg-surface-base rounded-lg border border-surface-border text-[11px] text-text-muted">
                              <span><strong className="text-[#22d3ee]">Proj</strong> median</span>
                              <span><strong className="text-[#818cf8]">Ceil</strong> {result.has_sim_data ? 'P90 sim' : 'est. ×1.3'}</span>
                              {result.has_floor_data && <span><strong className="text-[#fb923c]">Floor</strong> P10 sim</span>}
                              <span><strong className="text-[#a78bfa]">Value</strong> proj/sal×1k</span>
                              <span><strong className="text-success">Score</strong> composite{result.scoring_weights && <span className="ml-1 text-[9px] text-text-muted"> ({Math.round(result.scoring_weights.w_proj*100)}P/{Math.round(result.scoring_weights.w_value*100)}V/{Math.round(result.scoring_weights.w_own*100)}O)</span>}</span>
                              <span><strong className="text-[#c4b5fd]">All ??</strong> applies to all lineups</span>
                              <span className="px-1.5 py-px bg-success-muted text-success rounded font-semibold">STACK</span>
                              <span className="px-1.5 py-px bg-[#1e3a5f] text-[#38bdf8] rounded font-semibold">CORR</span>
                              <span className="ml-auto text-[11px]">
                                <strong className="text-text-primary">{result.current_salary.toLocaleString()}</strong>/{result.salary_cap.toLocaleString()}
                                {' � '}<span style={{ color: result.salary_cap - result.current_salary < 2000 ? '#ef4444' : '#22c55e' }}>{(result.salary_cap - result.current_salary).toLocaleString()} left</span>
                              </span>
                            </div>
                            {result.swaps.map((swap, i) => (
                              <SwapBlock key={i} swap={swap} onUseCandidate={handleUseCandidate} onUseCandidateAll={handleUseCandidateAll} multiLineupMode={true} />
                            ))}
                            <CandidatePoolInspector
                              result={result}
                              lineupLabel={activeLu.label}
                              lineupPlayers={activeLu.players}
                              scratchedNamesSet={scratchedNamesSet}
                              lockedNamesSet={new Set(lockedNames.map((n: string) => n.toLowerCase()))}
                              poolExcludedPlayers={poolExcludedPlayers}
                              onUseCandidate={handleUseCandidate}
                              onToggleExclude={handlePoolToggleExclude}
                              site={site}
                            />
                          </>
                        )}

                        {/* Batch errors/results */}
                        {batchError && (
                          <div className="px-3.5 py-2.5 bg-[#450a0a] border border-[#7f1d1d] rounded-lg text-xs text-[#fca5a5]">? {batchError}</div>
                        )}
                        {batchResult && (
                          <div className="px-4 py-2.5 bg-[#052e16] border border-[#166534] rounded-lg text-xs text-[#bbf7d0] flex justify-between items-center flex-wrap gap-2">
                            <span>
                              ✅ <strong className="text-success">{batchResult.total_lineups} lineups</strong> processed —{' '}
                              {batchResult.swap_log.reduce((s, l) => s + l.swaps_made, 0)} swaps applied
                              {batchResult.swap_log.some(l => l.swaps_failed > 0) && (
                                <span className="text-[#fca5a5] ml-1.5">
                                  · {batchResult.swap_log.reduce((s, l) => s + l.swaps_failed, 0)} failed
                                </span>
                              )}
                            </span>
                            <button onClick={handleDownloadBatch} className="px-3.5 py-1.5 rounded border-none text-xs font-bold cursor-pointer bg-[#16a34a] text-white hover:bg-[#15803d] transition-colors">
                              ⬇ Download {batchResult.site} Upload CSV
                            </button>
                          </div>
                        )}
                      </>
                    )
                  })()}
                </div>
              </div>

            ) : (
              /* --- No lineups yet: single lineup entry card --- */
              <>
                <div className={cardCls}>
                  <div className="flex justify-between items-center mb-3.5 flex-wrap gap-2">
                    <div className="text-sm font-bold text-text-secondary uppercase tracking-[0.05em]">Your Lineup</div>
                    <div className="text-xs text-text-muted">
                      Use <strong className="text-[#60a5fa]">? Import Entry CSV</strong> in the header to load all lineups at once
                    </div>
                  </div>

                  {importError && (
                    <div className="mb-3 px-3 py-2 bg-[#450a0a] border border-danger rounded text-[#fca5a5] text-xs">{importError}</div>
                  )}

                  <LineupBuilder
                    site={site}
                    lineupText={lineupText}
                    setLineupText={handleLineupTextChange}
                    scratchedText={scratchedText}
                    setScratchedText={setScratchedText}
                  />

                  <div className="mt-5 flex gap-2.5 items-center flex-wrap">
                    <button
                      onClick={handleFindSwaps}
                      disabled={!file || loading}
                      className={cn('px-6 py-[9px] rounded-lg border-none text-sm font-bold text-white transition-colors',
                        !file || loading ? 'bg-surface-border opacity-60 cursor-not-allowed' : 'bg-primary hover:bg-primary-hover cursor-pointer'
                      )}
                    >
                      {loading ? 'Finding Swaps...' : 'Find Best Swaps'}
                    </button>
                    {randomness > 0 && result && (
                      <button onClick={handleFindSwaps} disabled={loading} className="px-4 py-[9px] rounded-lg border border-[#5b21b6] text-sm font-bold cursor-pointer bg-transparent text-[#a78bfa] hover:bg-[#5b21b6]/10 transition-colors">Re-roll</button>
                    )}
                    <button onClick={() => { setLineupText(''); setScratchedText(''); setResult(null); setError(null) }} className={secBtnCls}>Clear</button>
                    {lineupText.trim() && <button onClick={handleExportLineup} className={secBtnCls}>Export Lineup</button>}
                    <div className="w-px h-8 bg-surface-border mx-1" />
                    <div className="flex items-center gap-[7px]">
                      <span className="text-[11px] text-text-muted">Variance</span>
                      <input type="range" min={0} max={100} step={5} value={randomness} onChange={e => { setRandomness(Number(e.target.value)); setResult(null) }} style={{ width: 80, accentColor: randomness > 0 ? '#a78bfa' : '#334155', cursor: 'pointer' }} />
                      <span className={cn("text-xs font-bold min-w-[30px]", randomness === 0 ? "text-text-muted" : "text-[#a78bfa]")}>{randomness}%</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="text-[11px] text-text-muted">Show</span>
                      <input type="number" min={5} max={50} step={5} value={nCandidates} onChange={e => setNCandidates(Math.max(5, Math.min(50, Number(e.target.value))))} className="w-[52px] bg-surface-border text-text-primary border border-surface-border rounded px-1.5 py-1 text-xs" />
                    </div>
                    <select value={sortBy} onChange={e => setSortBy(e.target.value as typeof sortBy)} className="bg-surface-border text-text-secondary border border-surface-border rounded px-2 py-[5px] text-xs cursor-pointer outline-none">
                      <option value="swap_score">Sort: Score</option>
                      <option value="projection">Sort: Proj</option>
                      <option value="ceiling">Sort: Ceiling</option>
                      <option value="value">Sort: Value</option>
                      <option value="own">Sort: Own% (low)</option>
                      <option value="floor">Sort: Floor</option>
                    </select>
                    {result && (() => {
                      const used = result.current_salary; const cap = result.salary_cap; const pct = Math.min(100, (used / cap) * 100); const remaining = cap - used
                      return (
                        <div className="flex items-center gap-2 ml-auto">
                          <div className="w-[100px] h-[6px] bg-surface-border rounded-full overflow-hidden">
                            <div className="h-full rounded-full transition-[width] duration-300" style={{ width: `${pct}%`, background: pct > 95 ? '#ef4444' : pct > 85 ? '#f97316' : '#22c55e' }} />
                          </div>
                          <span className="text-[11px] text-text-muted">
                            <strong className="text-text-primary">{used.toLocaleString()}</strong>/{cap.toLocaleString()} � <span style={{ color: remaining < 2000 ? '#ef4444' : '#22c55e' }}>{remaining.toLocaleString()} left</span>
                          </span>
                        </div>
                      )
                    })()}
                  </div>
                </div>

                {error && (
                  <div className="bg-[#450a0a] border border-danger rounded-lg px-4 py-2.5 mb-4 text-[#fca5a5] text-sm">{error}</div>
                )}

                {result && (
                  <>
                    <div className="flex gap-4 flex-wrap px-3.5 py-2 mb-3 bg-surface-base rounded-lg border border-surface-border text-[11px] text-text-muted">
                      <span><strong className="text-[#22d3ee]">Proj</strong> median FPTS</span>
                      <span><strong className="text-[#818cf8]">Ceil</strong> {result.has_sim_data ? 'P90 sim' : 'est. ceiling ×1.3'}</span>
                      {result.has_floor_data && <span><strong className="text-[#fb923c]">Floor</strong> P10 sim</span>}
                      <span><strong className="text-[#a78bfa]">Value</strong> proj/salary×1000</span>
                      <span><strong className="text-success">Score</strong> composite{result.scoring_weights && <span className="ml-1 text-[9px] text-text-muted"> ({Math.round(result.scoring_weights.w_proj*100)}P/{Math.round(result.scoring_weights.w_value*100)}V/{Math.round(result.scoring_weights.w_own*100)}O)</span>}</span>
                      <span className="px-1.5 py-px bg-success-muted text-success rounded font-semibold">STACK</span> same team
                      <span className="px-1.5 py-px bg-[#1e3a5f] text-[#38bdf8] rounded font-semibold">CORR</span> same game
                    </div>
                    {result.swaps.map((swap, i) => (
                      <SwapBlock key={i} swap={swap} onUseCandidate={handleUseCandidate} multiLineupMode={false} />
                    ))}

                    {/* Comparison for single-lineup manual swaps */}
                    {swapComparisons.has(activeLineupIdx) && (
                      <SwapResultComparison
                        comparison={swapComparisons.get(activeLineupIdx)!}
                        lineupLabel="Your Lineup"
                      />
                    )}
                    <CandidatePoolInspector
                      result={result}
                      lineupLabel="Your Lineup"
                      lineupPlayers={lineupText.split('\n').map((s: string) => s.trim()).filter(Boolean)}
                      scratchedNamesSet={scratchedNamesSet}
                      lockedNamesSet={new Set(lockedNames.map((n: string) => n.toLowerCase()))}
                      poolExcludedPlayers={poolExcludedPlayers}
                      onUseCandidate={handleUseCandidate}
                      onToggleExclude={handlePoolToggleExclude}
                      site={site}
                    />
                  </>
                )}

                {!loading && !result && !error && (
                  <div className="flex flex-col items-center justify-center py-[60px] gap-3.5 text-surface-border">
                    <div className="text-[40px] opacity-30">🔄</div>
                    <div className="text-sm font-semibold text-text-muted">No results yet</div>
                    <div className="text-xs text-surface-border text-center max-w-[400px]">
                      Click <strong className="text-[#60a5fa]">? Import Entry CSV</strong> in the header to load your submitted lineups, mark who got scratched, then click Find Best Swaps.
                    </div>
                    <div className="text-xs text-surface-border text-center max-w-[420px]">
                      Need fresh lineups?{' '}
                      <button onClick={() => setMode('reoptimize')} className="bg-transparent border-none cursor-pointer text-success text-xs font-bold underline p-0">
                        Switch to Re-Optimize
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}

            {/* Exposure summary � shown when lineups are loaded */}
            {/* Batch swap results table — full-width, outside 2-panel */}
            {batchResult && (
              <BatchSwapResultsTable
                batchResult={batchResult}
                activeLineupIdx={activeLineupIdx}
                onSelectLineup={idx => { setActiveLineupIdx(idx); setResult(null) }}
                salaryCap={batchResult.swap_log[0]?.salary_cap ?? (site === 'FD' ? 60000 : 50000)}
              />
            )}

            <ExposurePanel lineups={managedLineups} scratchedNamesSet={scratchedNamesSet} lockedNames={lockedNames} />
          </>
        )}
      </div>
    </div>
  )
}
