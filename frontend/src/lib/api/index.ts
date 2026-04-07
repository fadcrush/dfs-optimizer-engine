import { authFetch, getApiErrorMessage } from '@/lib/auth'

export type FDLineup = {
  lineup_num: number
  total_salary: number
  projected_points: number
  total_ownership?: number
  PG?: string
  PG_2?: string
  SG?: string
  SG_2?: string
  SF?: string
  SF_2?: string
  PF?: string
  PF_2?: string
  C?: string
  // DraftKings slots
  G?: string
  F?: string
  UTIL?: string
  [key: string]: string | number | undefined
}

export type FDOptimizerResponse = {
  message?: string
  total_lineups: number
  lineups: FDLineup[]
  stats?: {
    avg_projection?: number
    avg_salary?: number
    projection_range?: string
  }
  download_file?: string
  simulation_summary?: Array<Record<string, number>>
}

// ---------------------------------------------------------------------------
// Projections
// ---------------------------------------------------------------------------

export type PlayerProjection = {
  dfs_id: string
  name: string
  position: string
  team: string
  opponent: string
  salary: number
  projection: number
  floor: number
  ceiling: number
  std_dev: number
  value: number
  ownership: number
}

export type ProjectionsResponse = {
  success: boolean
  projections: PlayerProjection[]
  stats: {
    total_players: number
    avg_projection: number
    total_salary_available: number
    slate_file: string
    site: string
    sport: string
  }
  algorithm: string
  error?: string
}

// ---------------------------------------------------------------------------
// Analytics
// ---------------------------------------------------------------------------

export type RoiSummary = {
  total_invested: number
  total_won: number
  profit: number
  roi_percentage: number
  total_contests: number
  roi_by_type: Record<string, { invested: number; won: number; profit: number; roi: number; count: number }>
  period_days: number | 'all_time'
}

export type AccuracyReport = {
  total_projections: number
  mae: number | null
  rmse: number | null
  bias: number | null
  period_days: number | 'all_time'
  site: string
  by_day: Array<{ date: string; count: number; mae: number; rmse: number; bias: number }>
}

export type OwnershipAccuracyReport = {
  total_players: number
  mae: number | null
  bias: number | null
  /** % of 30%+ owned players where prediction was within ±8pp */
  chalk_accuracy: number | null
  /** % of predictions from GBR model (vs percentile fallback) */
  model_pct: number | null
  period_days: number | 'all_time'
  site: string
  note?: string
  by_day: Array<{ date: string; count: number; mae: number; bias: number }>
}

export type OwnershipModelStatus = {
  training_rows: { DK: number; FD: number }
  models: {
    DK: { exists: boolean; trained_at?: string; age_days?: number }
    FD: { exists: boolean; trained_at?: string; age_days?: number }
  }
  min_rows_required: number
  ready: { DK: boolean; FD: boolean }
}

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'
const fetch = authFetch

// ---------------------------------------------------------------------------
// Optimizer
// ---------------------------------------------------------------------------

export async function runFDOptimizer(
  file: File,
  params: {
    numLineups: number
    minSalary: number
    maxSalary: number
    maxExposure: number
    numUnique?: number
    site?: 'FD' | 'DK'
    outTeams?: string[]
    outPlayers?: string[]
    chalkThreshold?: number
    projectionOverrides?: Record<string, number>
    lockedPlayers?: string[]
  }
): Promise<{ success: boolean; data?: FDOptimizerResponse; error?: string; planGated?: boolean }> {
  const form = new FormData()
  form.append('file', file)

  const url = new URL(API_BASE + '/api/optimizer/run')
  url.searchParams.set('site', params.site ?? 'FD')
  url.searchParams.set('sport', 'NBA')
  url.searchParams.set('n_lineups', String(params.numLineups))
  if (params.numUnique !== undefined) {
    url.searchParams.set('num_unique', String(params.numUnique))
  }
  if (params.outTeams && params.outTeams.length > 0) {
    url.searchParams.set('out_teams', params.outTeams.join(','))
  }
  if (params.outPlayers && params.outPlayers.length > 0) {
    url.searchParams.set('out_players', params.outPlayers.join(','))
  }
  if (params.chalkThreshold && params.chalkThreshold > 0) {
    url.searchParams.set('chalk_threshold', String(params.chalkThreshold))
  }
  if (params.projectionOverrides && Object.keys(params.projectionOverrides).length > 0) {
    url.searchParams.set('projection_overrides', JSON.stringify(params.projectionOverrides))
  }
  if (params.lockedPlayers && params.lockedPlayers.length > 0) {
    url.searchParams.set('locked_players', params.lockedPlayers.join(','))
  }

  const res = await fetch(url.toString(), { method: 'POST', body: form })
  if (!res.ok) {
    const errorText = await getApiErrorMessage(res)
    return { success: false, error: errorText, planGated: res.status === 403 }
  }
  const data = (await res.json()) as FDOptimizerResponse
  return { success: true, data }
}

// ---------------------------------------------------------------------------
// Async optimizer — submits to Celery queue, returns task_id for polling
// ---------------------------------------------------------------------------

export type AsyncOptimizerSubmitResponse = {
  task_id: string
  status: string
}

export async function runOptimizerAsync(
  file: File,
  params: {
    numLineups: number
    maxExposure: number
    numUnique?: number
    site?: 'FD' | 'DK'
    contestType?: string
    outTeams?: string[]
    outPlayers?: string[]
    chalkThreshold?: number
    projectionOverrides?: Record<string, number>
    lockedPlayers?: string[]
  },
): Promise<{ success: boolean; taskId?: string; error?: string; planGated?: boolean }> {
  const form = new FormData()
  form.append('file', file)

  const url = new URL(API_BASE + '/api/optimizer/run-async')
  url.searchParams.set('site', params.site ?? 'FD')
  url.searchParams.set('sport', 'NBA')
  url.searchParams.set('n_lineups', String(params.numLineups))
  url.searchParams.set('max_exposure', String(params.maxExposure))
  if (params.numUnique !== undefined) {
    url.searchParams.set('num_unique', String(params.numUnique))
  }
  if (params.contestType) {
    url.searchParams.set('contest_type', params.contestType)
  }
  if (params.outTeams && params.outTeams.length > 0) {
    url.searchParams.set('out_teams', params.outTeams.join(','))
  }
  if (params.outPlayers && params.outPlayers.length > 0) {
    url.searchParams.set('out_players', params.outPlayers.join(','))
  }
  if (params.chalkThreshold && params.chalkThreshold > 0) {
    url.searchParams.set('chalk_threshold', String(params.chalkThreshold))
  }
  if (params.projectionOverrides && Object.keys(params.projectionOverrides).length > 0) {
    url.searchParams.set('projection_overrides', JSON.stringify(params.projectionOverrides))
  }
  if (params.lockedPlayers && params.lockedPlayers.length > 0) {
    url.searchParams.set('locked_players', params.lockedPlayers.join(','))
  }

  const res = await fetch(url.toString(), { method: 'POST', body: form })
  if (!res.ok) {
    const errorText = await getApiErrorMessage(res)
    return { success: false, error: errorText, planGated: res.status === 403 }
  }
  const data = (await res.json()) as AsyncOptimizerSubmitResponse
  return { success: true, taskId: data.task_id }
}

// ---------------------------------------------------------------------------
// One-click full pipeline
// ---------------------------------------------------------------------------

export type PipelineSteps = {
  slate_uploaded: string
  injuries_refreshed: boolean
  props_refreshed: Record<string, unknown>
  projections_generated: number
  ownership_model: string
  lineups_generated: number
}

export type FullPipelineResponse = FDOptimizerResponse & {
  pipeline_steps?: PipelineSteps
  projection_source?: string
}

export async function runFullPipeline(
  file: File,
  params: {
    site: 'FD' | 'DK'
    numLineups: number
    maxExposure?: number
    numUnique?: number
    contestType?: string
    outTeams?: string[]
    outPlayers?: string[]
    enableStacking?: boolean
    minGameStack?: number
    bringBackCount?: number
    refreshProps?: boolean
    chalkThreshold?: number
    projectionOverrides?: Record<string, number>
    lockedPlayers?: string[]
  }
): Promise<{ success: boolean; data?: FullPipelineResponse; error?: string; planGated?: boolean }> {
  const form = new FormData()
  form.append('file', file)

  const url = new URL(API_BASE + '/api/pipeline/run-full')
  url.searchParams.set('site', params.site)
  url.searchParams.set('sport', 'NBA')
  url.searchParams.set('n_lineups', String(params.numLineups))
  url.searchParams.set('max_exposure', String(params.maxExposure ?? 0.6))
  url.searchParams.set('contest_type', params.contestType ?? 'gpp')
  url.searchParams.set('enable_stacking', String(params.enableStacking ?? true))
  url.searchParams.set('min_game_stack', String(params.minGameStack ?? 2))
  url.searchParams.set('bring_back_count', String(params.bringBackCount ?? 1))
  url.searchParams.set('refresh_props', String(params.refreshProps ?? true))
  if (params.numUnique !== undefined) {
    url.searchParams.set('num_unique', String(params.numUnique))
  }
  if (params.chalkThreshold && params.chalkThreshold > 0) {
    url.searchParams.set('chalk_threshold', String(params.chalkThreshold))
  }
  if (params.outTeams && params.outTeams.length > 0) {
    url.searchParams.set('out_teams', params.outTeams.join(','))
  }
  if (params.outPlayers && params.outPlayers.length > 0) {
    url.searchParams.set('out_players', params.outPlayers.join(','))
  }
  if (params.projectionOverrides && Object.keys(params.projectionOverrides).length > 0) {
    url.searchParams.set('projection_overrides', JSON.stringify(params.projectionOverrides))
  }
  if (params.lockedPlayers && params.lockedPlayers.length > 0) {
    url.searchParams.set('locked_players', params.lockedPlayers.join(','))
  }

  const res = await fetch(url.toString(), { method: 'POST', body: form })
  if (!res.ok) {
    const errMsg = await getApiErrorMessage(res)
    return { success: false, error: errMsg, planGated: res.status === 403 }
  }
  const data = (await res.json()) as FullPipelineResponse
  return { success: true, data }
}

export async function downloadLineups(
  fileName: string
): Promise<{ success: boolean; data?: string; error?: string }> {
  const res = await fetch(API_BASE + `/api/optimizer/download/${fileName}`)
  if (!res.ok) {
    const errorText = await getApiErrorMessage(res)
    return { success: false, error: errorText }
  }
  const body = await res.json()
  return { success: true, data: body.data }
}

// ---------------------------------------------------------------------------
// Projections
// ---------------------------------------------------------------------------

export async function runProjections(
  file: File,
  site: 'DK' | 'FD' = 'DK',
  sport: 'NBA' | 'NFL' = 'NBA',
): Promise<{ success: boolean; data?: ProjectionsResponse; error?: string; rateLimited?: boolean }> {
  const form = new FormData()
  form.append('file', file)

  const url = new URL(API_BASE + '/api/projections/run')
  url.searchParams.set('site', site)
  url.searchParams.set('sport', sport)

  const res = await fetch(url.toString(), { method: 'POST', body: form })
  if (!res.ok) {
    const errMsg = await getApiErrorMessage(res)
    return { success: false, error: errMsg, rateLimited: res.status === 429 }
  }
  const raw = await res.json()
  // Backend returns { success, projections, stats, algorithm, ... }
  // Guard: if no projections array, treat as failure
  if (!raw.projections || !Array.isArray(raw.projections)) {
    return { success: false, error: raw.error ?? raw.detail ?? 'No projections returned' }
  }
  const data: ProjectionsResponse = {
    success: true,
    projections: raw.projections,
    stats: {
      total_players: raw.stats?.total_players ?? raw.total_projections ?? raw.projections.length,
      avg_projection: raw.stats?.avg_projection ?? 0,
      total_salary_available: raw.stats?.total_salary_available ?? 0,
      slate_file: raw.stats?.slate_file ?? raw.slate_file?.file_name ?? '',
      site: raw.stats?.site ?? site,
      sport: raw.stats?.sport ?? sport,
    },
    algorithm: raw.algorithm ?? 'canonical',
  }
  return { success: true, data }
}

// ---------------------------------------------------------------------------
// Analytics
// ---------------------------------------------------------------------------

export async function getAnalyticsRoi(
  days = 30,
  site?: string,
): Promise<{ success: boolean; data?: RoiSummary; error?: string }> {
  const url = new URL(API_BASE + '/analytics/roi')
  url.searchParams.set('days', String(days))
  if (site) url.searchParams.set('site', site)
  const res = await fetch(url.toString())
  if (!res.ok) return { success: false, error: await res.text() }
  return { success: true, data: await res.json() }
}

export async function getAnalyticsAccuracy(
  days = 30,
  site = 'DK',
): Promise<{ success: boolean; data?: AccuracyReport; error?: string }> {
  const url = new URL(API_BASE + '/analytics/accuracy')
  url.searchParams.set('days', String(days))
  url.searchParams.set('site', site)
  const res = await fetch(url.toString())
  if (!res.ok) return { success: false, error: await res.text() }
  return { success: true, data: await res.json() }
}

export type ContestEntryPayload = {
  contest_date: string        // YYYY-MM-DD
  contest_type: 'gpp' | 'double_up' | 'cash' | 'winner_take_all'
  site: 'DK' | 'FD'
  entry_fee: number
  payout?: number
  final_rank?: number
  total_entries?: number
  notes?: string
}

export async function postContestResult(
  body: ContestEntryPayload,
): Promise<{ success: boolean; error?: string }> {
  const res = await fetch(API_BASE + '/analytics/contest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) return { success: false, error: await getApiErrorMessage(res) }
  return { success: true }
}

// ---------------------------------------------------------------------------
// Late Swap
// ---------------------------------------------------------------------------

export type SwapCandidate = {
  name: string
  resolved_name: string
  position: string
  team: string
  game_info: string
  is_same_game: boolean
  is_same_team: boolean
  salary: number
  projection: number
  ceiling: number
  floor: number          // Sim_P10 or projection * 0.75 estimate
  value: number          // proj / salary * 1000
  proj_delta: number
  salary_delta: number
  new_total_salary: number
  dfs_id: string
  own: number
  swap_score: number     // composite rank score 0–1 (may exceed 1 with correlation bonus)
}

export type SwapResult = {
  scratched_player: string
  resolved_player: string
  scratched_salary: number
  scratched_proj: number
  scratched_position: string
  salary_budget: number
  candidates: SwapCandidate[]
  total_candidates_found: number
}

export type LateSwapResponse = {
  success: boolean
  site: string
  lineup: string[]
  current_salary: number
  salary_cap: number
  has_sim_data: boolean
  has_floor_data: boolean
  sort_by: string
  contest_type: string
  scoring_weights: { w_proj: number; w_value: number; w_own: number }
  swaps: SwapResult[]
}

export async function lateSwap(
  file: File,
  params: {
    site: 'DK' | 'FD'
    sport?: string
    lineup: string[]
    slotLabels?: string[]
    scratched: string[]
    lockedPlayers?: string[]
    randomness?: number   // 0.0–1.0
    n_candidates?: number // max candidates per scratched player (default 15)
    sort_by?: 'swap_score' | 'projection' | 'value' | 'ceiling' | 'own' | 'floor'
    w_proj?: number       // scoring weight for projection (0–1)
    w_value?: number      // scoring weight for value (0–1)
    w_own?: number        // scoring weight for ownership (0–1)
    contest_type?: 'balanced' | 'cash' | 'gpp' | 'tournament'
  }
): Promise<{ success: boolean; data?: LateSwapResponse; error?: string; planGated?: boolean }> {
  const form = new FormData()
  form.append('file', file)

  const url = new URL(API_BASE + '/api/optimizer/late-swap')
  url.searchParams.set('site', params.site)
  url.searchParams.set('sport', params.sport ?? 'NBA')
  url.searchParams.set('lineup', params.lineup.join(','))
  if (params.slotLabels && params.slotLabels.length > 0) {
    url.searchParams.set('slot_labels', params.slotLabels.join(','))
  }
  url.searchParams.set('scratched', params.scratched.join(','))
  if (params.lockedPlayers && params.lockedPlayers.length > 0) {
    url.searchParams.set('locked_players', params.lockedPlayers.join(','))
  }
  if (params.randomness != null && params.randomness > 0) {
    url.searchParams.set('randomness', String(params.randomness))
  }
  if (params.n_candidates != null) {
    url.searchParams.set('n_candidates', String(params.n_candidates))
  }
  if (params.sort_by) {
    url.searchParams.set('sort_by', params.sort_by)
  }
  if (params.w_proj != null) url.searchParams.set('w_proj', String(params.w_proj))
  if (params.w_value != null) url.searchParams.set('w_value', String(params.w_value))
  if (params.w_own != null) url.searchParams.set('w_own', String(params.w_own))
  if (params.contest_type) url.searchParams.set('contest_type', params.contest_type)

  const res = await fetch(url.toString(), { method: 'POST', body: form })
  if (!res.ok) {
    const errMsg = await getApiErrorMessage(res)
    return { success: false, error: errMsg, planGated: res.status === 403 }
  }
  const data = (await res.json()) as LateSwapResponse
  return { success: true, data }
}

// ---------------------------------------------------------------------------
// Batch Late Swap — auto-apply best swaps to all lineups and export upload CSV
// ---------------------------------------------------------------------------

export type BatchSwapEntry = {
  scratched: string
  replacement: string | null
  salary_delta?: number
  proj_delta?: number
  note?: string
}

export type BatchSwapLineupLog = {
  lineup_index: number
  total_salary: number
  salary_cap: number
  players_kept: number
  swaps_made: number
  swaps_failed: number
  swaps: BatchSwapEntry[]
}

export type BatchLateSwapResponse = {
  success: boolean
  site: string
  total_lineups: number
  swap_log: BatchSwapLineupLog[]
  download_file: string
}

export async function batchLateSwap(
  file: File,
  params: {
    site: 'DK' | 'FD'
    sport?: string
    lineups: string[][]   // array of player-name arrays
    scratched: string[]
    lockedPlayers?: string[]  // players to force-keep (started-game locks)
    w_proj?: number
    w_value?: number
    w_own?: number
    contest_type?: 'balanced' | 'cash' | 'gpp' | 'tournament'
    diversity_factor?: number  // 0–1, default 0.3
  }
): Promise<{ success: boolean; data?: BatchLateSwapResponse; error?: string; planGated?: boolean }> {
  const form = new FormData()
  form.append('file', file)

  const url = new URL(API_BASE + '/api/optimizer/batch-late-swap')
  url.searchParams.set('site', params.site)
  url.searchParams.set('sport', params.sport ?? 'NBA')
  url.searchParams.set('lineups', JSON.stringify(params.lineups))
  url.searchParams.set('scratched', params.scratched.join(','))
  if (params.lockedPlayers && params.lockedPlayers.length > 0) {
    url.searchParams.set('locked_players', params.lockedPlayers.join(','))
  }
  if (params.w_proj != null) url.searchParams.set('w_proj', String(params.w_proj))
  if (params.w_value != null) url.searchParams.set('w_value', String(params.w_value))
  if (params.w_own != null) url.searchParams.set('w_own', String(params.w_own))
  if (params.contest_type) url.searchParams.set('contest_type', params.contest_type)
  if (params.diversity_factor != null) url.searchParams.set('diversity_factor', String(params.diversity_factor))

  const ctrl = new AbortController()
  const tid = setTimeout(() => ctrl.abort(), 90_000)   // 90s — pipeline can take ~80s
  let res: Response
  try {
    res = await fetch(url.toString(), { method: 'POST', body: form, signal: ctrl.signal })
  } catch (err) {
    clearTimeout(tid)
    const msg = err instanceof DOMException && err.name === 'AbortError'
      ? 'Batch swap timed out (>90s) — try again or reduce lineups'
      : 'Network error during batch swap'
    return { success: false, error: msg }
  }
  clearTimeout(tid)
  if (!res.ok) {
    const errMsg = await getApiErrorMessage(res)
    return { success: false, error: errMsg, planGated: res.status === 403 }
  }
  const data = (await res.json()) as BatchLateSwapResponse
  return { success: true, data }
}

// ---------------------------------------------------------------------------
// Parse Lineup CSV — resolve DK/FD entry CSV player IDs → names
// ---------------------------------------------------------------------------

export type ParsedLineup = {
  players: string[]
  slot_labels: string[]
}

export type ParseLineupsResponse = {
  success: boolean
  site: string
  count: number
  lineups: ParsedLineup[]
}

export async function parseLineupCSV(
  entryFile: File,
  slateFile: File,
  site: 'DK' | 'FD',
): Promise<{ success: boolean; data?: ParseLineupsResponse; error?: string }> {
  const form = new FormData()
  form.append('entry_file', entryFile)
  form.append('slate_file', slateFile)

  const url = new URL(API_BASE + '/api/optimizer/parse-lineup-csv')
  url.searchParams.set('site', site)

  const res = await fetch(url.toString(), { method: 'POST', body: form })
  if (!res.ok) {
    const errMsg = await getApiErrorMessage(res)
    return { success: false, error: errMsg }
  }
  const data = (await res.json()) as ParseLineupsResponse
  return { success: true, data }
}

// ---------------------------------------------------------------------------
// Contests (import + contest-level ROI/accuracy)
// ---------------------------------------------------------------------------

export type ContestRoiSummary = {
  total_entries: number
  total_invested: number
  total_won: number
  profit: number
  roi_pct: number
  site: string
  period_days: number
}

export type ContestAccuracyReport = {
  mae: number | null
  rmse: number | null
  bias: number | null
  total_players: number
  period_days: number
  sport: string
}

export async function importContestFile(
  file: File,
): Promise<{ success: boolean; data?: { imported: number }; error?: string }> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(API_BASE + '/api/contests/import', { method: 'POST', body: form })
  if (!res.ok) return { success: false, error: await getApiErrorMessage(res) }
  return { success: true, data: await res.json() }
}

export async function getContestsRoi(
  days = 30,
  site = 'all',
): Promise<{ success: boolean; data?: ContestRoiSummary; error?: string }> {
  const url = new URL(API_BASE + '/api/contests/roi')
  url.searchParams.set('days', String(days))
  url.searchParams.set('site', site)
  const res = await fetch(url.toString())
  if (!res.ok) return { success: false, error: await getApiErrorMessage(res) }
  return { success: true, data: await res.json() }
}

export async function getContestsAccuracy(
  days = 30,
  sport = 'NBA',
): Promise<{ success: boolean; data?: ContestAccuracyReport; error?: string }> {
  const url = new URL(API_BASE + '/api/contests/accuracy')
  url.searchParams.set('days', String(days))
  url.searchParams.set('sport', sport)
  const res = await fetch(url.toString())
  if (!res.ok) return { success: false, error: await getApiErrorMessage(res) }
  return { success: true, data: await res.json() }
}

// ---------------------------------------------------------------------------
// Ownership accuracy & model management
// ---------------------------------------------------------------------------

export async function getOwnershipAccuracy(
  days = 30,
  site = 'DK',
): Promise<{ success: boolean; data?: OwnershipAccuracyReport; error?: string }> {
  const url = new URL(API_BASE + '/analytics/ownership-accuracy')
  url.searchParams.set('days', String(days))
  url.searchParams.set('site', site)
  const res = await fetch(url.toString())
  if (!res.ok) return { success: false, error: await getApiErrorMessage(res) }
  return { success: true, data: await res.json() }
}

export async function getOwnershipModelStatus(): Promise<{
  success: boolean
  data?: OwnershipModelStatus
  error?: string
}> {
  const res = await fetch(API_BASE + '/analytics/ownership-model-status')
  if (!res.ok) return { success: false, error: await getApiErrorMessage(res) }
  return { success: true, data: await res.json() }
}

export async function trainOwnershipModel(
  site = 'DK',
  sport = 'NBA',
): Promise<{ success: boolean; data?: { success: boolean; message: string }; error?: string }> {
  const url = new URL(API_BASE + '/analytics/train-ownership-model')
  url.searchParams.set('site', site)
  url.searchParams.set('sport', sport)
  const res = await fetch(url.toString(), { method: 'POST' })
  if (!res.ok) return { success: false, error: await getApiErrorMessage(res) }
  return { success: true, data: await res.json() }
}

export async function importOwnershipActuals(
  file: File,
  site: string,
  gameDate: string,
  contestType = 'gpp',
  slateId = '',
): Promise<{ success: boolean; data?: { imported: number; skipped: number; source: string }; error?: string }> {
  const form = new FormData()
  form.append('file', file)
  const url = new URL(API_BASE + '/analytics/import-ownership-actuals')
  url.searchParams.set('site', site)
  url.searchParams.set('game_date', gameDate)
  url.searchParams.set('contest_type', contestType)
  if (slateId) url.searchParams.set('slate_id', slateId)
  const res = await fetch(url.toString(), { method: 'POST', body: form })
  if (!res.ok) return { success: false, error: await getApiErrorMessage(res) }
  return { success: true, data: await res.json() }
}

export async function seedOwnershipFromSlate(
  file: File,
  site: string,
  gameDate: string,
  contestType = 'gpp',
): Promise<{ success: boolean; data?: { seeded: number; note: string; top_players: Array<{ name: string; fppg: number; est_own: number }> }; error?: string }> {
  const form = new FormData()
  form.append('file', file)
  const url = new URL(API_BASE + '/analytics/seed-ownership-from-slate')
  url.searchParams.set('site', site)
  url.searchParams.set('game_date', gameDate)
  url.searchParams.set('contest_type', contestType)
  const res = await fetch(url.toString(), { method: 'POST', body: form })
  if (!res.ok) return { success: false, error: await res.text() }
  return { success: true, data: await res.json() }
}

// ---------------------------------------------------------------------------
// Scheduler status
// ---------------------------------------------------------------------------

export type SchedulerStatus = {
  running: boolean
  jobs: Array<{ id: string; name: string; next_run: string | null }>
}

export async function getSchedulerStatus(): Promise<{
  success: boolean
  data?: SchedulerStatus
  error?: string
}> {
  const res = await fetch(API_BASE + '/health')
  if (!res.ok) return { success: false, error: await res.text() }
  const data = await res.json()
  return { success: true, data: data.scheduler ?? { running: false, jobs: [] } }
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

export function downloadFile(content: string, fileName: string) {
  const blob = new Blob([content], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = fileName
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/** Convert a list of objects to a CSV string with a header row. */
export function toCsv(rows: Record<string, unknown>[]): string {
  if (!rows.length) return ''
  const headers = Object.keys(rows[0])
  const lines = [
    headers.join(','),
    ...rows.map(row =>
      headers.map(h => {
        const val = String(row[h] ?? '')
        return val.includes(',') ? `"${val}"` : val
      }).join(',')
    ),
  ]
  return lines.join('\n')
}
