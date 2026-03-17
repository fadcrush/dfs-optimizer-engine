/**
 * Tests for the Candidate Pool engine + diagnostics.
 *
 * Run with: npx jest (after `npm i --save-dev jest ts-jest @types/jest`)
 */

import { ExclusionReason } from '../types'
import {
  buildCandidatePool,
  applyPoolFilters,
  sortPoolItems,
  computePoolSummary,
  type CandidatePoolContext,
} from '../candidatePool'
import {
  getCandidateDiagnostic,
  getStatusBadge,
} from '../candidateDiagnostics'
import type { SwapResult } from '@/lib/api'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** Minimal SwapCandidate builder */
function makeCandidate(
  overrides: Partial<{
    name: string
    position: string
    team: string
    game_info: string
    salary: number
    projection: number
    ceiling: number
    value: number
    own: number
    swap_score: number
    proj_delta: number
    salary_delta: number
    new_total_salary: number
    dfs_id: string
    is_same_game: boolean
    is_same_team: boolean
  }> = {},
) {
  return {
    name:            overrides.name          ?? 'Player A',
    resolved_name:   overrides.name          ?? 'Player A',
    position:        overrides.position      ?? 'PG',
    team:            overrides.team          ?? 'BOS',
    game_info:       overrides.game_info     ?? 'BOS@LAL 7:30PM ET',
    salary:          overrides.salary        ?? 8000,
    projection:      overrides.projection    ?? 30,
    ceiling:         overrides.ceiling       ?? 45,
    value:           overrides.value         ?? 3.75,
    own:             overrides.own           ?? 20,
    swap_score:      overrides.swap_score    ?? 0.7,
    proj_delta:      overrides.proj_delta    ?? 5,
    salary_delta:    overrides.salary_delta  ?? -1000,
    new_total_salary: overrides.new_total_salary ?? 48000,
    dfs_id:          overrides.dfs_id        ?? 'id_a',
    is_same_game:    overrides.is_same_game  ?? false,
    is_same_team:    overrides.is_same_team  ?? false,
  }
}

/** Minimal SwapResult builder */
function makeSwapResult(overrides: {
  resolved_player?: string
  scratched_player?: string
  salary_budget?: number
  candidates?: ReturnType<typeof makeCandidate>[]
}): SwapResult {
  return {
    scratched_player:   overrides.scratched_player  ?? 'LeBron James',
    resolved_player:    overrides.resolved_player   ?? 'LeBron James',
    scratched_salary:   10000,
    scratched_proj:     35,
    scratched_position: 'SF',
    salary_budget:      overrides.salary_budget ?? 10000,
    candidates:         (overrides.candidates ?? [makeCandidate()]) as any,
    total_candidates_found: overrides.candidates?.length ?? 1,
  }
}

function makeCtx(overrides: Partial<CandidatePoolContext> = {}): CandidatePoolContext {
  return {
    result: {
      swaps:          [makeSwapResult({})],
      current_salary: 47000,
      salary_cap:     50000,
    },
    lineupPlayers:    ['LeBron James', 'Anthony Davis', 'Jaylen Brown', 'Jayson Tatum', 'Kristaps Porzingis', 'Derrick White', 'Al Horford'],
    scratchedNamesSet: new Set(['lebron james']),
    lockedNamesSet:   new Set(),
    poolExcludedPlayers: new Set(),
    site:             'DK',
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// buildCandidatePool
// ---------------------------------------------------------------------------

describe('buildCandidatePool', () => {
  test('returns one item per unique candidate name', () => {
    const ctx = makeCtx()
    const items = buildCandidatePool(ctx)
    expect(items).toHaveLength(1)
    expect(items[0].name).toBe('Player A')
  })

  test('deduplicates the same player across multiple swaps', () => {
    const sharedCandidateName = 'Jaylen Brown'
    const ctx = makeCtx({
      result: {
        swaps: [
          makeSwapResult({
            resolved_player: 'LeBron James',
            salary_budget: 10000,
            candidates: [makeCandidate({ name: sharedCandidateName, position: 'SF' })],
          }),
          makeSwapResult({
            resolved_player: 'Anthony Davis',
            salary_budget: 10000,
            candidates: [makeCandidate({ name: sharedCandidateName, position: 'SF' })],
          }),
        ],
        current_salary: 47000,
        salary_cap: 50000,
      },
      scratchedNamesSet: new Set(['lebron james', 'anthony davis']),
    })
    const items = buildCandidatePool(ctx)
    expect(items).toHaveLength(1)
    expect(items[0].candidateFor).toHaveLength(2)
    expect(items[0].candidateFor).toContain('LeBron James')
    expect(items[0].candidateFor).toContain('Anthony Davis')
  })

  test('marks player ALREADY_IN_LINEUP when not scratched but in lineup', () => {
    const ctx = makeCtx({
      result: {
        swaps: [makeSwapResult({ candidates: [makeCandidate({ name: 'Jaylen Brown' })] })],
        current_salary: 47000,
        salary_cap: 50000,
      },
      lineupPlayers: ['LeBron James', 'Jaylen Brown'],
      scratchedNamesSet: new Set(['lebron james']),
    })
    const items = buildCandidatePool(ctx)
    const jb = items.find(i => i.name === 'Jaylen Brown')!
    expect(jb.reasonExcluded).toBe(ExclusionReason.ALREADY_IN_LINEUP)
    expect(jb.eligible).toBe(false)
  })

  test('marks player SALARY_TOO_HIGH when salary > budget', () => {
    const ctx = makeCtx({
      result: {
        swaps: [makeSwapResult({
          salary_budget: 6000,
          candidates: [makeCandidate({ name: 'Giannis', salary: 9500 })],
        })],
        current_salary: 44000,
        salary_cap: 50000,
      },
      lineupPlayers: ['LeBron James'],
      scratchedNamesSet: new Set(['lebron james']),
    })
    const items = buildCandidatePool(ctx)
    const g = items.find(i => i.name === 'Giannis')!
    expect(g.reasonExcluded).toBe(ExclusionReason.SALARY_TOO_HIGH)
    expect(g.fitsSalary).toBe(false)
    expect(g.eligible).toBe(false)
  })

  test('marks player SCRATCHED_OR_OUT when on scratch list', () => {
    const ctx = makeCtx({
      result: {
        swaps: [makeSwapResult({
          candidates: [makeCandidate({ name: 'Kawhi Leonard' })],
        })],
        current_salary: 47000,
        salary_cap: 50000,
      },
      scratchedNamesSet: new Set(['lebron james', 'kawhi leonard']),
    })
    const items = buildCandidatePool(ctx)
    const kawhi = items.find(i => i.name === 'Kawhi Leonard')!
    expect(kawhi.reasonExcluded).toBe(ExclusionReason.SCRATCHED_OR_OUT)
  })

  test('marks player USER_EXCLUDED when in poolExcludedPlayers', () => {
    const ctx = makeCtx({
      poolExcludedPlayers: new Set(['player a']),
    })
    const items = buildCandidatePool(ctx)
    expect(items[0].reasonExcluded).toBe(ExclusionReason.USER_EXCLUDED)
    expect(items[0].poolIncluded).toBe(false)
  })

  test('eligible player has no reasonExcluded', () => {
    const ctx = makeCtx()
    const items = buildCandidatePool(ctx)
    expect(items[0].eligible).toBe(true)
    expect(items[0].reasonExcluded).toBeUndefined()
  })

  test('computes leverage as projection - own * 0.4', () => {
    const ctx = makeCtx({
      result: {
        swaps: [makeSwapResult({ candidates: [makeCandidate({ projection: 30, own: 20 })] })],
        current_salary: 47000,
        salary_cap: 50000,
      },
    })
    const items = buildCandidatePool(ctx)
    expect(items[0].leverage).toBeCloseTo(30 - 20 * 0.4, 5) // 22
  })

  test('DK includes G/F/UTIL slots for guards and forwards', () => {
    const ctx = makeCtx({
      result: {
        swaps: [makeSwapResult({ candidates: [makeCandidate({ position: 'PG', name: 'Guard' })] })],
        current_salary: 47000,
        salary_cap: 50000,
      },
      site: 'DK',
    })
    const items = buildCandidatePool(ctx)
    expect(items[0].eligibleSlots).toContain('G')
    expect(items[0].eligibleSlots).toContain('UTIL')
  })
})

// ---------------------------------------------------------------------------
// applyPoolFilters
// ---------------------------------------------------------------------------

describe('applyPoolFilters', () => {
  function pool() {
    const ctx = makeCtx({
      result: {
        swaps: [makeSwapResult({
          salary_budget: 10000,
          candidates: [
            makeCandidate({ name: 'Jaylen Brown', position: 'SF', team: 'BOS', projection: 30, salary: 8000 }),
            makeCandidate({ name: 'Giannis Antetokounmpo', position: 'PF', team: 'MIL', salary: 9500, projection: 40 }),
            makeCandidate({ name: 'Kristaps Porzingis', position: 'C', team: 'BOS', projection: 18 }),
          ],
        })],
        current_salary: 42000,
        salary_cap: 50000,
      },
      scratchedNamesSet: new Set(['lebron james']),
      lineupPlayers: ['LeBron James'],
    })
    return buildCandidatePool(ctx)
  }

  test('search by partial name (case-insensitive)', () => {
    const items = applyPoolFilters(pool(), { search: 'jaylen' })
    expect(items).toHaveLength(1)
    expect(items[0].name).toBe('Jaylen Brown')
  })

  test('search by team', () => {
    const items = applyPoolFilters(pool(), { search: 'MIL' })
    expect(items).toHaveLength(1)
    expect(items[0].name).toBe('Giannis Antetokounmpo')
  })

  test('position filter', () => {
    const items = applyPoolFilters(pool(), { position: 'C' })
    expect(items.every(i => i.positions.includes('C'))).toBe(true)
  })

  test('includedOnly returns only eligible items', () => {
    const ctx = makeCtx({
      poolExcludedPlayers: new Set(['player a']),
    })
    const allItems = buildCandidatePool(ctx)
    const filtered = applyPoolFilters(allItems, { includedOnly: true })
    expect(filtered.every(i => i.eligible)).toBe(true)
  })

  test('excludedOnly returns only ineligible items', () => {
    const ctx = makeCtx({
      poolExcludedPlayers: new Set(['player a']),
    })
    const allItems = buildCandidatePool(ctx)
    const filtered = applyPoolFilters(allItems, { excludedOnly: true })
    expect(filtered.every(i => !i.eligible)).toBe(true)
  })

  test('empty filters returns all items', () => {
    const items = pool()
    expect(applyPoolFilters(items, {})).toHaveLength(items.length)
  })
})

// ---------------------------------------------------------------------------
// sortPoolItems
// ---------------------------------------------------------------------------

describe('sortPoolItems', () => {
  function twoItems() {
    const ctx = makeCtx({
      result: {
        swaps: [makeSwapResult({
          salary_budget: 10000,
          candidates: [
            makeCandidate({ name: 'High Proj', projection: 40, ceiling: 55 }),
            makeCandidate({ name: 'Low Proj',  projection: 20, ceiling: 30 }),
          ],
        })],
        current_salary: 42000,
        salary_cap: 50000,
      },
      lineupPlayers: ['LeBron James'],
      scratchedNamesSet: new Set(['lebron james']),
    })
    return buildCandidatePool(ctx)
  }

  test('sorts by projection descending', () => {
    const sorted = sortPoolItems(twoItems(), 'projection', 'desc')
    expect(sorted[0].name).toBe('High Proj')
    expect(sorted[1].name).toBe('Low Proj')
  })

  test('sorts by projection ascending', () => {
    const sorted = sortPoolItems(twoItems(), 'projection', 'asc')
    expect(sorted[0].name).toBe('Low Proj')
  })

  test('sorts by ceiling descending', () => {
    const sorted = sortPoolItems(twoItems(), 'ceiling', 'desc')
    expect(sorted[0].name).toBe('High Proj') // ceiling 55
  })
})

// ---------------------------------------------------------------------------
// computePoolSummary
// ---------------------------------------------------------------------------

describe('computePoolSummary', () => {
  test('correctly counts eligible, excluded, fits-salary', () => {
    const ctx = makeCtx({
      result: {
        swaps: [makeSwapResult({
          salary_budget: 8000,
          candidates: [
            makeCandidate({ name: 'Fits',      salary: 7000 }),       // eligible
            makeCandidate({ name: 'TooRich',   salary: 9000 }),       // SALARY_TOO_HIGH
          ],
        })],
        current_salary: 43000,
        salary_cap: 50000,
      },
      lineupPlayers: ['LeBron James'],
      scratchedNamesSet: new Set(['lebron james']),
    })
    const items = buildCandidatePool(ctx)
    const result = { swaps: ctx.result.swaps, current_salary: 43000, salary_cap: 50000 }
    const summary = computePoolSummary(items, result)
    expect(summary.totalCandidates).toBe(2)
    expect(summary.eligibleCount).toBe(1)
    expect(summary.excludedCount).toBe(1)
    expect(summary.fitSalaryCount).toBe(1)
    expect(summary.remainingSalary).toBe(50000 - 43000)
  })
})

// ---------------------------------------------------------------------------
// getCandidateDiagnostic
// ---------------------------------------------------------------------------

describe('getCandidateDiagnostic', () => {
  function itemWithReason(reason: ExclusionReason) {
    return {
      name: 'Test Player',
      team: 'BOS',
      opponent: '',
      positions: ['PG'],
      salary: 8000,
      projection: 30,
      ownership: 20,
      value: 3.75,
      leverage: 22,
      gameInfo: 'BOS@LAL',
      gameStarted: false,
      status: 'healthy' as const,
      eligibleSlots: ['PG', 'G', 'UTIL'],
      fitsSalary: reason !== ExclusionReason.SALARY_TOO_HIGH,
      poolIncluded: reason !== ExclusionReason.USER_EXCLUDED,
      eligible: false,
      reasonExcluded: reason,
      candidateFor: ['LeBron James'],
      ceiling: 45,
      swapScore: 0.7,
      salaryDelta: -1000,
      projDelta: 5,
      newTotalSalary: 48000,
      dfsId: 'id',
      isSameGame: false,
      isSameTeam: false,
    }
  }

  test('returns null for eligible item', () => {
    const item = { ...itemWithReason(ExclusionReason.USER_EXCLUDED), reasonExcluded: undefined, eligible: true }
    expect(getCandidateDiagnostic(item as any)).toBeNull()
  })

  test('SALARY_TOO_HIGH includes salary in message', () => {
    const d = getCandidateDiagnostic(itemWithReason(ExclusionReason.SALARY_TOO_HIGH))!
    expect(d).not.toBeNull()
    expect(d.message).toContain('8,000')
    expect(d.suggestion).toBeDefined()
  })

  test('ALREADY_IN_LINEUP returns correct message', () => {
    const d = getCandidateDiagnostic(itemWithReason(ExclusionReason.ALREADY_IN_LINEUP))!
    expect(d.message).toContain('already in this lineup')
  })

  test('USER_EXCLUDED mentions re-include action', () => {
    const d = getCandidateDiagnostic(itemWithReason(ExclusionReason.USER_EXCLUDED))!
    expect(d.suggestion).toMatch(/re-include/i)
  })

  test('SCRATCHED_OR_OUT message mentions scratch list', () => {
    const d = getCandidateDiagnostic(itemWithReason(ExclusionReason.SCRATCHED_OR_OUT))!
    expect(d.message).toMatch(/scratch list/i)
  })
})

// ---------------------------------------------------------------------------
// getStatusBadge
// ---------------------------------------------------------------------------

describe('getStatusBadge', () => {
  function item(reason?: ExclusionReason) {
    return {
      name: 'X', team: '',  opponent: '', positions: [], salary: 0, projection: 0,
      ownership: 0, value: 0, leverage: 0, gameInfo: '', gameStarted: false, status: 'healthy' as const,
      eligibleSlots: [], fitsSalary: true, poolIncluded: true,
      eligible: reason === undefined,
      reasonExcluded: reason,
      candidateFor: [], ceiling: 0, swapScore: 0, salaryDelta: 0, projDelta: 0,
      newTotalSalary: 0, dfsId: '', isSameGame: false, isSameTeam: false,
    }
  }

  test('eligible item gets LEGAL badge', () => {
    const b = getStatusBadge(item())
    expect(b.label).toBe('LEGAL')
  })

  test('OUT item gets OUT badge', () => {
    const b = getStatusBadge(item(ExclusionReason.SCRATCHED_OR_OUT))
    expect(b.label).toBe('OUT')
  })

  test('USER_EXCLUDED item gets EXCLUDED badge', () => {
    const b = getStatusBadge(item(ExclusionReason.USER_EXCLUDED))
    expect(b.label).toBe('EXCLUDED')
  })

  test('SALARY_TOO_HIGH item gets OVER $ badge', () => {
    const b = getStatusBadge(item(ExclusionReason.SALARY_TOO_HIGH))
    expect(b.label).toBe('OVER $')
  })
})
