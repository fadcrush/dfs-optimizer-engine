// ---------------------------------------------------------------------------
// Late Swap — Result Comparison & Failure Diagnostics Types
// ---------------------------------------------------------------------------

export enum FailureReason {
  NO_ELIGIBLE_REPLACEMENT    = 'NO_ELIGIBLE_REPLACEMENT',
  SALARY_CAP_CONSTRAINT      = 'SALARY_CAP_CONSTRAINT',
  POSITION_CONSTRAINT        = 'POSITION_CONSTRAINT',
  ALL_CANDIDATES_STARTED     = 'ALL_CANDIDATES_STARTED',
  PLAYER_POOL_TOO_RESTRICTIVE = 'PLAYER_POOL_TOO_RESTRICTIVE',
  ROSTER_RULE_CONFLICT       = 'ROSTER_RULE_CONFLICT',
}

export const FAILURE_LABELS: Record<FailureReason, string> = {
  [FailureReason.NO_ELIGIBLE_REPLACEMENT]:    'No Eligible Replacement',
  [FailureReason.SALARY_CAP_CONSTRAINT]:      'Salary Cap Constraint',
  [FailureReason.POSITION_CONSTRAINT]:        'Position Constraint',
  [FailureReason.ALL_CANDIDATES_STARTED]:     'All Candidates Started',
  [FailureReason.PLAYER_POOL_TOO_RESTRICTIVE]: 'Pool Too Restrictive',
  [FailureReason.ROSTER_RULE_CONFLICT]:       'Roster Rule Conflict',
}

export const FAILURE_SUGGESTIONS: Record<FailureReason, string[]> = {
  [FailureReason.NO_ELIGIBLE_REPLACEMENT]: [
    'Verify your lineup and scratch list are correct',
    'Ensure the correct slate file is loaded',
  ],
  [FailureReason.SALARY_CAP_CONSTRAINT]: [
    'Expand the player pool to include lower-salaried options',
    'Remove a locked player to create salary flexibility',
    'Swap a different player to free up cap room',
  ],
  [FailureReason.POSITION_CONSTRAINT]: [
    'Check that an eligible player at the required position exists in the pool',
    'Remove position-level exclusions if any were applied',
  ],
  [FailureReason.ALL_CANDIDATES_STARTED]: [
    'Use a player from a game that has not yet started',
    'Check the locked players list and remove if over-restrictive',
  ],
  [FailureReason.PLAYER_POOL_TOO_RESTRICTIVE]: [
    'Confirm GTD players in the Injury Report so they become eligible',
    'Remove team or position filters from the player pool',
    'Ensure the correct slate file is uploaded',
  ],
  [FailureReason.ROSTER_RULE_CONFLICT]: [
    'Check for duplicate position violations',
    'Verify the lineup follows site roster rules',
  ],
}

/** A single swap diff entry — one player removed and one added (or a failure). */
export interface SwapDiff {
  /** Player scratched / removed */
  removed: string
  /** Replacement added — null if swap failed */
  added: string | null
  /** Roster position of the removed player */
  position?: string
  /** Salary of removed player */
  removedSalary?: number
  /** Salary of added player */
  addedSalary?: number
  /** Change in projected FPTS for this swap */
  projDelta: number
  /** Change in salary (positive = spent more) */
  salaryDelta: number
  /** Whether this specific swap succeeded */
  success: boolean
  /** Reason if failed */
  failureReason?: FailureReason
  /** Human-readable failure message */
  failureMessage?: string
}

/** Full comparison for one lineup — original vs post-swap. */
export interface LineupSwapComparison {
  /** Index in managedLineups array */
  lineupIdx: number
  /** Individual player-level diffs */
  diffs: SwapDiff[]
  /** Final salary after all swaps */
  finalSalary: number
  /** Original salary before swaps */
  originalSalary: number
  /** Salary cap for the site */
  salaryCap: number
  /** Sum of all proj_deltas */
  totalProjDelta: number
  /** Sum of all salary_deltas */
  totalSalaryDelta: number
  /** How many swaps succeeded */
  successCount: number
  /** How many swaps failed */
  failureCount: number
  /** How comparison was generated */
  source: 'manual' | 'batch'
  /** Non-blocking warnings */
  warnings: string[]
}

/** Export pre-flight validation error. */
export interface ExportValidationError {
  lineup: string
  message: string
  severity: 'error' | 'warning'
}

// ---------------------------------------------------------------------------
// Candidate Pool Inspector — data model
// ---------------------------------------------------------------------------

export enum ExclusionReason {
  GAME_ALREADY_STARTED  = 'GAME_ALREADY_STARTED',
  SCRATCHED_OR_OUT      = 'SCRATCHED_OR_OUT',
  NOT_IN_PLAYER_POOL    = 'NOT_IN_PLAYER_POOL',
  SALARY_TOO_HIGH       = 'SALARY_TOO_HIGH',
  POSITION_INELIGIBLE   = 'POSITION_INELIGIBLE',
  ALREADY_IN_LINEUP     = 'ALREADY_IN_LINEUP',
  FAILED_STRATEGY_FILTER = 'FAILED_STRATEGY_FILTER',
  USER_EXCLUDED         = 'USER_EXCLUDED',
}

export const EXCLUSION_LABELS: Record<ExclusionReason, string> = {
  [ExclusionReason.GAME_ALREADY_STARTED]:   'Game already started',
  [ExclusionReason.SCRATCHED_OR_OUT]:       'Player scratched / out',
  [ExclusionReason.NOT_IN_PLAYER_POOL]:     'Not in player pool',
  [ExclusionReason.SALARY_TOO_HIGH]:        'Does not fit remaining salary',
  [ExclusionReason.POSITION_INELIGIBLE]:    'Position not eligible for open slots',
  [ExclusionReason.ALREADY_IN_LINEUP]:      'Already in this lineup',
  [ExclusionReason.FAILED_STRATEGY_FILTER]: 'Excluded by strategy filter',
  [ExclusionReason.USER_EXCLUDED]:          'Excluded by player pool',
}

/** One row in the Candidate Pool Inspector table. */
export interface CandidatePoolItem {
  /** Display name */
  name: string
  team: string
  /** Raw game info string from API */
  opponent: string
  /** Parsed positions array, e.g. ['PG', 'SG'] */
  positions: string[]
  salary: number
  projection: number
  /** Projected ownership % */
  ownership: number
  /** proj / salary * 1000 */
  value: number
  /** High-leverage score: projection minus ownership-weighted projection */
  leverage: number
  /** Full game info string */
  gameInfo: string
  /** True when the player's game has already tipped off */
  gameStarted: boolean
  status: 'healthy' | 'gtd' | 'out' | 'scratched' | 'confirmed_starter'
  /** Roster slots this candidate can fill given current lineup context */
  eligibleSlots: string[]
  /** Salary fits under the remaining cap for the open slot */
  fitsSalary: boolean
  /** Not user-excluded */
  poolIncluded: boolean
  /** All eligibility checks pass — legal swap target */
  eligible: boolean
  reasonExcluded?: ExclusionReason
  /** Which scratched players this candidate can replace */
  candidateFor: string[]
  // --- SwapCandidate fields preserved for use-action wiring ---
  ceiling: number
  swapScore: number
  salaryDelta: number
  projDelta: number
  newTotalSalary: number
  dfsId: string
  isSameGame: boolean
  isSameTeam: boolean
}

export interface CandidatePoolFilters {
  position?: string
  includedOnly?: boolean
  excludedOnly?: boolean
  fitSalaryOnly?: boolean
  hideUserExcluded?: boolean
  search?: string
}

export type CandidatePoolSortKey =
  | 'projection'
  | 'ceiling'
  | 'salary'
  | 'ownership'
  | 'value'
  | 'leverage'
  | 'swapScore'
