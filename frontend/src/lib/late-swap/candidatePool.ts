// ---------------------------------------------------------------------------
// Candidate Pool Engine
// Builds a flat, deduplicated candidate universe for the selected lineup.
// Operates entirely on the LateSwapResponse already held in component state.
// ---------------------------------------------------------------------------

import type { SwapResult, SwapCandidate } from '@/lib/api'
import {
  ExclusionReason,
  type CandidatePoolItem,
  type CandidatePoolFilters,
  type CandidatePoolSortKey,
} from './types'

// ---------------------------------------------------------------------------
// Context passed in by the page
// ---------------------------------------------------------------------------

export interface CandidatePoolContext {
  /** Current swap result for the selected lineup */
  result: {
    swaps: SwapResult[]
    current_salary: number
    salary_cap: number
  }
  /** Full player roster of the selected lineup (post-swap names) */
  lineupPlayers: string[]
  /** Scratched players (lower-cased) */
  scratchedNamesSet: Set<string>
  /** Locked / force-kept players (lower-cased) */
  lockedNamesSet: Set<string>
  /** User-excluded from pool (lower-cased player names) */
  poolExcludedPlayers: Set<string>
  /** DK or FD — affects eligible-slot logic */
  site: 'DK' | 'FD'
}

// ---------------------------------------------------------------------------
// Eligible-slot mapping
// ---------------------------------------------------------------------------

function computeEligibleSlots(positions: string[], site: 'DK' | 'FD'): string[] {
  const slots: string[] = [...positions]
  if (site === 'DK') {
    if (positions.some(p => ['PG', 'SG'].includes(p))) slots.push('G')
    if (positions.some(p => ['SF', 'PF'].includes(p))) slots.push('F')
    slots.push('UTIL') // Any non-C can fill UTIL; keep it simple
  }
  if (site === 'FD') {
    // FD uses positional duplication (PG x2, SG x2, etc.) — slots are just positions
  }
  return [...new Set(slots)]
}

// ---------------------------------------------------------------------------
// Core builder — run once per result change
// ---------------------------------------------------------------------------

export function buildCandidatePool(ctx: CandidatePoolContext): CandidatePoolItem[] {
  const { result, lineupPlayers, scratchedNamesSet, lockedNamesSet, poolExcludedPlayers, site } = ctx

  // Deduplicate candidates across all swaps, merging candidateFor arrays
  const map = new Map<string, { c: SwapCandidate; candidateFor: string[]; minBudget: number }>()

  for (const swap of result.swaps) {
    for (const c of swap.candidates) {
      const key = c.name.toLowerCase()
      if (map.has(key)) {
        const entry = map.get(key)!
        if (!entry.candidateFor.includes(swap.resolved_player)) {
          entry.candidateFor.push(swap.resolved_player)
        }
        entry.minBudget = Math.min(entry.minBudget, swap.salary_budget)
      } else {
        map.set(key, {
          c,
          candidateFor: [swap.resolved_player],
          minBudget: swap.salary_budget,
        })
      }
    }
  }

  // Players currently in the lineup (non-scratched)
  const activeLineupSet = new Set(
    lineupPlayers
      .filter(p => !scratchedNamesSet.has(p.toLowerCase()))
      .map(p => p.toLowerCase()),
  )

  const items: CandidatePoolItem[] = []

  for (const [nameKey, { c, candidateFor, minBudget }] of map) {
    const isUserExcluded  = poolExcludedPlayers.has(nameKey)
    const isAlreadyInLineup = activeLineupSet.has(nameKey)
    const isScratched     = scratchedNamesSet.has(nameKey)

    // Choose exclusion reason (priority order)
    let reasonExcluded: ExclusionReason | undefined
    if (isScratched) {
      reasonExcluded = ExclusionReason.SCRATCHED_OR_OUT
    } else if (isAlreadyInLineup) {
      reasonExcluded = ExclusionReason.ALREADY_IN_LINEUP
    } else if (isUserExcluded) {
      reasonExcluded = ExclusionReason.USER_EXCLUDED
    }

    const fitsSalary = c.salary <= minBudget
    if (!fitsSalary && !reasonExcluded) {
      reasonExcluded = ExclusionReason.SALARY_TOO_HIGH
    }

    const positions = c.position
      .split('/')
      .map(p => p.trim())
      .filter(Boolean)

    const eligibleSlots = computeEligibleSlots(positions, site)
    const eligible = reasonExcluded === undefined

    // Leverage heuristic: reward high projection AND low ownership
    // leverage > 0 means this player is underowned relative to their upside
    const leverage = c.projection - c.own * 0.4

    items.push({
      name:           c.name,
      team:           c.team,
      opponent:       c.game_info,
      positions,
      salary:         c.salary,
      projection:     c.projection,
      ownership:      c.own,
      value:          c.value,
      leverage,
      gameInfo:       c.game_info,
      gameStarted:    false,  // backend already strips started players
      status:         'healthy',
      eligibleSlots,
      fitsSalary,
      poolIncluded:   !isUserExcluded,
      eligible,
      reasonExcluded,
      candidateFor,
      ceiling:        c.ceiling,
      swapScore:      c.swap_score,
      salaryDelta:    c.salary_delta,
      projDelta:      c.proj_delta,
      newTotalSalary: c.new_total_salary,
      dfsId:          c.dfs_id,
      isSameGame:     c.is_same_game,
      isSameTeam:     c.is_same_team,
    })
  }

  return items
}

// ---------------------------------------------------------------------------
// Filter
// ---------------------------------------------------------------------------

export function applyPoolFilters(
  items: CandidatePoolItem[],
  filters: CandidatePoolFilters,
): CandidatePoolItem[] {
  let out = items

  if (filters.search?.trim()) {
    const q = filters.search.toLowerCase()
    out = out.filter(i =>
      i.name.toLowerCase().includes(q) || i.team.toLowerCase().includes(q),
    )
  }
  if (filters.position) {
    out = out.filter(i => i.positions.includes(filters.position!))
  }
  if (filters.includedOnly) {
    out = out.filter(i => i.eligible)
  }
  if (filters.excludedOnly) {
    out = out.filter(i => !i.eligible)
  }
  if (filters.fitSalaryOnly) {
    out = out.filter(i => i.fitsSalary)
  }
  if (filters.hideUserExcluded) {
    out = out.filter(i => i.reasonExcluded !== ExclusionReason.USER_EXCLUDED)
  }

  return out
}

// ---------------------------------------------------------------------------
// Sort
// ---------------------------------------------------------------------------

export function sortPoolItems(
  items: CandidatePoolItem[],
  key: CandidatePoolSortKey,
  dir: 'asc' | 'desc',
): CandidatePoolItem[] {
  return [...items].sort((a, b) => {
    let va = 0, vb = 0
    switch (key) {
      case 'projection': va = a.projection; vb = b.projection; break
      case 'ceiling':    va = a.ceiling;    vb = b.ceiling;    break
      case 'salary':     va = a.salary;     vb = b.salary;     break
      case 'ownership':  va = a.ownership;  vb = b.ownership;  break
      case 'value':      va = a.value;      vb = b.value;      break
      case 'leverage':   va = a.leverage;   vb = b.leverage;   break
      case 'swapScore':  va = a.swapScore;  vb = b.swapScore;  break
    }
    const cmp = va - vb
    return dir === 'desc' ? -cmp : cmp
  })
}

// ---------------------------------------------------------------------------
// Summary stats (for the summary bar)
// ---------------------------------------------------------------------------

export interface PoolSummary {
  totalCandidates: number
  eligibleCount: number
  excludedCount: number
  fitSalaryCount: number
  /** Tightest salary budget across all open swaps */
  remainingSalary: number
  /** All open scratched positions */
  openPositions: string[]
}

export function computePoolSummary(
  items: CandidatePoolItem[],
  result: { swaps: SwapResult[] },
): PoolSummary {
  const remainingSalary = Math.min(
    ...result.swaps.map(s => s.salary_budget),
    99999,
  )
  const openPositions = [...new Set(result.swaps.map(s => s.scratched_position))]

  return {
    totalCandidates: items.length,
    eligibleCount:   items.filter(i => i.eligible).length,
    excludedCount:   items.filter(i => !i.eligible).length,
    fitSalaryCount:  items.filter(i => i.fitsSalary).length,
    remainingSalary,
    openPositions,
  }
}
