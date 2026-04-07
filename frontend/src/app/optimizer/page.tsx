'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import {
  runFDOptimizer,
  runOptimizerAsync,
  runFullPipeline,
  downloadLineups,
  downloadFile,
  type FDOptimizerResponse,
  type FullPipelineResponse,
  type PipelineSteps,
  type FDLineup,
} from '@/lib/api'
import { useTaskStatus } from '@/lib/hooks/useTaskStatus'
import { formatSalary, formatProjection, cn } from '@/lib/utils'
import { useLatestSlate } from '@/hooks/useLatestSlate'
import { SlateSelector } from '@/components/shared/SlateSelector'
import { SkeletonTable } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { ContestModeSelector, CONTEST_MODE_PRESETS, type ContestMode, type ContestModeConfig } from '@/components/shared/ContestModeSelector'
import { LockFadeControl, type ProjectionPlayer } from '@/components/shared/LockFadeControl'
import { CopyLineupButton } from '@/components/shared/CopyLineupButton'
import { InjuryAlertBanner } from '@/components/shared/InjuryAlertBanner'
import { getInjurySummary } from '@/lib/api/slates'
import type { InjurySummary } from '@/lib/api/slates'
import {
  PlayerPoolPanel,
  parseCSVToPool,
  getPoolExcludedNames,
  getPoolProjectionOverrides,
  getPoolLockedNames,
  validatePool,
  type PlayerPoolMap,
  type PoolStatus,
} from '@/components/optimizer/PlayerPoolPanel'

// ============================================================================
// Configuration
// ============================================================================

const MAX_FILE_SIZE_MB = 10
const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

// Required CSV columns (case-insensitive, accepts common aliases)
const REQUIRED_COLUMNS = [
  { name: 'Name', aliases: ['name', 'player', 'nickname', 'playername', 'player_name'] },
  { name: 'Position', aliases: ['position', 'pos', 'roster position'] },
  { name: 'Team', aliases: ['team', 'teamabbrev', 'team_abbrev'] },
  { name: 'Salary', aliases: ['salary', 'sal'] },
  { name: 'Projection', aliases: ['projection', 'proj', 'fppg', 'fpts', 'points', 'avgpointspergame'] },
]

// FanDuel / DraftKings slot configs
const FD_SLOT_KEYS = ['PG', 'PG_2', 'SG', 'SG_2', 'SF', 'SF_2', 'PF', 'PF_2', 'C'] as const
const DK_SLOT_KEYS = ['PG', 'SG', 'SF', 'PF', 'C', 'G', 'F', 'UTIL'] as const

type SlotKey = (typeof FD_SLOT_KEYS)[number] | (typeof DK_SLOT_KEYS)[number]

const SITE_SLOT_CONFIG: Record<'FD' | 'DK', ReadonlyArray<string>> = {
  FD: FD_SLOT_KEYS,
  DK: DK_SLOT_KEYS,
}

const SALARY_CAPS = { FD: 60000, DK: 50000 } as const
const SALARY_DEFAULTS = { FD: 59000, DK: 47000 } as const

// ============================================================================
// CSV Validation
// ============================================================================

interface ValidationResult {
  valid: boolean
  error?: string
  missingColumns?: string[]
  foundColumns?: string[]
}

async function validateCSV(file: File): Promise<ValidationResult> {
  // Check file extension
  if (!file.name.toLowerCase().endsWith('.csv')) {
    return { valid: false, error: 'File must be a CSV (.csv extension)' }
  }

  // Check file size
  if (file.size > MAX_FILE_SIZE_BYTES) {
    return {
      valid: false,
      error: `File too large (${(file.size / 1024 / 1024).toFixed(1)}MB). Maximum size is ${MAX_FILE_SIZE_MB}MB.`,
    }
  }

  // Check file is not empty
  if (file.size === 0) {
    return { valid: false, error: 'File is empty' }
  }

  // Read and parse headers
  try {
    const headerLine = await readFirstLine(file)
    if (!headerLine.trim()) {
      return { valid: false, error: 'CSV file has no header row' }
    }

    const headers = parseCSVLine(headerLine).map((h) => h.toLowerCase().trim())
    const foundColumns: string[] = []
    const missingColumns: string[] = []

    for (const required of REQUIRED_COLUMNS) {
      const found = required.aliases.some((alias) => headers.includes(alias))
      if (found) {
        foundColumns.push(required.name)
      } else {
        missingColumns.push(required.name)
      }
    }

    if (missingColumns.length > 0) {
      return {
        valid: false,
        error: `Missing required columns: ${missingColumns.join(', ')}`,
        missingColumns,
        foundColumns,
      }
    }

    return { valid: true, foundColumns }
  } catch (err) {
    return {
      valid: false,
      error: `Could not read CSV: ${err instanceof Error ? err.message : 'Unknown error'}`,
    }
  }
}

/** @deprecated kept for simulation page compat */
function cleanName(raw: string | number | undefined): string {
  if (raw == null) return '-'
  const s = String(raw)
  // Strip any legacy "ID:Name" prefix just in case
  const m = s.match(/^\d+:(.+)$/)
  return m ? m[1] : s
}

function readFirstLine(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (e) => {
      const content = e.target?.result as string
      const firstLine = content.split(/\r?\n/)[0] || ''
      resolve(firstLine)
    }
    reader.onerror = () => reject(new Error('Failed to read file'))
    // Only read first 10KB for header detection
    reader.readAsText(file.slice(0, 10240))
  })
}

function parseCSVLine(line: string): string[] {
  const result: string[] = []
  let current = ''
  let inQuotes = false

  for (let i = 0; i < line.length; i++) {
    const char = line[i]
    if (char === '"') {
      inQuotes = !inQuotes
    } else if (char === ',' && !inQuotes) {
      result.push(current)
      current = ''
    } else {
      current += char
    }
  }
  result.push(current)
  return result
}

// ============================================================================
// CSV Header Normalization
// ============================================================================

// Map of FanDuel/DraftKings column names to normalized names the backend expects
// IMPORTANT: fppg / avgpointspergame / points are intentionally excluded — the
// backend's canonical projection engine uses our own L10 game-log averages
// (stored in dfs_edge.duckdb) and must NEVER fall back to DK/FD stock numbers.
const HEADER_NORMALIZATION_MAP: Record<string, string> = {
  // Name variants -> Name
  'nickname': 'Name',
  'playername': 'Name',
  'player_name': 'Name',
  'player': 'Name',
  // Position variants -> Pos
  'roster position': 'Pos',
  'position': 'Pos',
  // Team variants -> Team
  'teamabbrev': 'Team',
  'team_abbrev': 'Team',
  // Only rename columns that represent OUR projections (user-supplied), never DK/FD stock averages
  'projection': 'Proj',
  'my proj': 'Proj',
  'ss proj': 'Proj',
}

/**
 * Normalizes CSV headers to match backend expectations.
 * FanDuel uses "FPPG" but backend expects "Proj".
 */
async function normalizeCSVHeaders(file: File): Promise<File> {
  const content = await file.text()
  const lines = content.split(/\r?\n/)

  if (lines.length === 0) return file

  const headerLine = lines[0]
  const headers = parseCSVLine(headerLine)

  let needsNormalization = false
  const normalizedHeaders = headers.map((header) => {
    const lowerHeader = header.toLowerCase().trim()
    const normalized = HEADER_NORMALIZATION_MAP[lowerHeader]
    if (normalized && normalized !== header) {
      needsNormalization = true
      return normalized
    }
    return header
  })

  // If no normalization needed, return original file
  if (!needsNormalization) return file

  // Rebuild CSV with normalized headers
  lines[0] = normalizedHeaders.join(',')
  const normalizedContent = lines.join('\n')

  return new File([normalizedContent], file.name, { type: 'text/csv' })
}

// ============================================================================
// Error Display Component
// ============================================================================

interface ErrorDisplayProps {
  message: string
  rawError?: string
}

function ErrorDisplay({ message, rawError }: ErrorDisplayProps) {
  const [showRaw, setShowRaw] = useState(false)

  return (
    <div className="bg-[#7f1d1d33] border border-[#ef4444] rounded-lg px-4 py-3 mb-5 flex gap-3 items-start">
      <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="#f87171" className="shrink-0 mt-0.5">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
      <div className="flex-1">
        <p className="m-0 text-[#fca5a5] font-semibold">{message}</p>
        {rawError && rawError !== message && (
          <div className="mt-2">
            <button onClick={() => setShowRaw(!showRaw)}
              className="bg-transparent border-none text-[#f87171] cursor-pointer text-xs underline p-0">
              {showRaw ? 'Hide' : 'Show'} technical details
            </button>
            {showRaw && (
              <pre className="mt-1.5 p-2 bg-black/30 rounded-[6px] text-[11px] text-[#fca5a5] overflow-x-auto">{rawError}</pre>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ============================================================================
// Run Parameters Component
// ============================================================================

interface RunParams {
  numLineups: number
  minSalary: number
  maxSalary: number
  maxExposure: number
  numUnique: number
  platform: string
  contestType: string
  site: 'FD' | 'DK'
}

function RunParametersDisplay({ params }: { params: RunParams }) {
  const chipCls = 'px-2.5 py-[3px] bg-surface-border border border-[#334155] rounded-[6px] text-xs text-text-muted'
  return (
    <div className="bg-[#0f172a] rounded-lg px-3.5 py-2.5 mb-3.5">
      <p className="m-0 mb-2 text-[11px] text-[#64748b] uppercase font-bold">Run Parameters</p>
      <div className="flex flex-wrap gap-2">
        <span className={chipCls}>Platform: <strong className="text-[#f1f5f9]">{params.platform}</strong></span>
        <span className={chipCls}>Lineups: <strong className="text-[#f1f5f9]">{params.numLineups}</strong></span>
        <span className={chipCls}>Salary: <strong className="text-[#f1f5f9]">{formatSalary(params.minSalary)}</strong> – <strong className="text-[#f1f5f9]">{formatSalary(params.maxSalary)}</strong></span>
        <span className={chipCls}>Max Exposure: <strong className="text-[#f1f5f9]">{(params.maxExposure * 100).toFixed(0)}%</strong></span>
        <span className={chipCls}>Num Unique: <strong className="text-[#f1f5f9]">{params.numUnique}</strong></span>
        <span className={chipCls}>Contest: <strong className="text-[#f1f5f9]">{params.contestType}</strong></span>
      </div>
    </div>
  )
}

// ============================================================================
// Main Page Component
// ============================================================================

export default function OptimizerPage() {
  // Form state
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [site, setSite] = useState<'FD' | 'DK'>('FD')
  const [numLineups, setNumLineups] = useState(20)
  const [minSalary, setMinSalary] = useState(59000)

  // UI state
  const [loading, setLoading] = useState(false)
  const [pipelineLoading, setPipelineLoading] = useState(false)
  const [pipelineSteps, setPipelineSteps] = useState<PipelineSteps | null>(null)
  const [validating, setValidating] = useState(false)
  const [error, setError] = useState<{ message: string; raw?: string } | null>(null)
  const [planGated, setPlanGated] = useState(false)
  const [validationStatus, setValidationStatus] = useState<string | null>(null)

  // Results state
  const [result, setResult] = useState<FDOptimizerResponse | null>(null)
  const [runParams, setRunParams] = useState<RunParams | null>(null)

  // Async task state — drives useTaskStatus polling
  const [taskId, setTaskId] = useState<string | null>(null)
  const taskStatus = useTaskStatus<FDOptimizerResponse>(taskId)
  const asyncLoading = taskStatus.status === 'queued' || taskStatus.status === 'running'

  // Contest mode + stacking
  const [contestMode, setContestMode] = useState<ContestMode>('gpp')
  const [enableStacking, setEnableStacking] = useState(true)
  const [minGameStack, setMinGameStack] = useState(2)
  const [bringBackCount, setBringBackCount] = useState(1)

  // Chalk auto-fade: players projected > this % owned are excluded from the pool
  const [chalkThreshold, setChalkThreshold] = useState(0)

  // Exposure & uniqueness controls
  const [maxExposure, setMaxExposure] = useState(50)   // percent (e.g. 50 = 50%)
  const [numUnique, setNumUnique] = useState(2)          // unique players across each lineup

  // Lock / fade — source of truth is poolMap; LockFadeControl reads/writes via these handlers
  // ── Player Pool ───────────────────────────────────────────────────────────
  const [playerPool, setPlayerPool] = useState<PlayerPoolMap>(new Map())

  const poolLockNames = getPoolLockedNames(playerPool)

  // ── Injury awareness ────────────────────────────────────────────────────
  const [injurySummary, setInjurySummary] = useState<InjurySummary | null>(null)
  const [injuryAutoExcluded, setInjuryAutoExcluded] = useState(0)

  // Fetch injury data and auto-exclude OUT players when pool changes
  const applyInjuryExclusions = useCallback(async (pool: PlayerPoolMap) => {
    if (pool.size === 0) return pool
    try {
      const data = await getInjurySummary()
      const outNames = new Set(
        data.players
          .filter(p => ['OUT', 'O'].includes(p.status.toUpperCase()))
          .map(p => p.player_name.toLowerCase())
      )
      if (outNames.size === 0) {
        setInjurySummary({
          out_count: data.out_count, questionable_count: data.questionable_count,
          doubtful_count: data.doubtful_count, probable_count: 0,
          out_players: [], questionable_players: [], doubtful_players: [],
          all_injuries: [], changed_since_export: 0, last_updated: data.timestamp,
        })
        return pool
      }

      // Auto-exclude OUT players from the pool
      const next = new Map(pool)
      let excluded = 0
      next.forEach((entry, id) => {
        if (outNames.has(entry.name.toLowerCase()) && entry.poolStatus === 'included') {
          next.set(id, { ...entry, poolStatus: 'excluded' as const })
          excluded++
        }
      })

      setInjuryAutoExcluded(excluded)
      setInjurySummary({
        out_count: data.out_count,
        questionable_count: data.questionable_count,
        doubtful_count: data.doubtful_count,
        probable_count: 0,
        out_players: data.players.filter(p => ['OUT', 'O'].includes(p.status.toUpperCase())).map(p => ({
          name: p.player_name, team: p.team, detail: p.detail, status: p.status,
        })),
        questionable_players: data.players.filter(p => ['QUESTIONABLE', 'Q', 'GTD'].includes(p.status.toUpperCase())).map(p => ({
          name: p.player_name, team: p.team, detail: p.detail, status: p.status,
        })),
        doubtful_players: data.players.filter(p => ['DOUBTFUL', 'D'].includes(p.status.toUpperCase())).map(p => ({
          name: p.player_name, team: p.team, detail: p.detail, status: p.status,
        })),
        all_injuries: [],
        changed_since_export: 0,
        last_updated: data.timestamp,
      })

      return next
    } catch {
      // Injury data unavailable — proceed without exclusions
      return pool
    }
  }, [])

  // Sync task result/error into page state when the async optimizer task completes
  useEffect(() => {
    if (taskStatus.status === 'done' && taskStatus.result) {
      setResult(taskStatus.result)
    } else if (taskStatus.status === 'error') {
      setError({
        message: 'Optimizer task failed. Check your inputs and try again.',
        raw: taskStatus.error ?? undefined,
      })
    }
  }, [taskStatus.status, taskStatus.result, taskStatus.error])

  // Sync LockFadeControl → pool: when name list changes, update poolStatus accordingly
  const handleLocksChange = (newLocks: string[]) => {
    const locksSet = new Set(newLocks.map(n => n.toLowerCase()))
    const next = new Map(playerPool)
    let changed = false
    next.forEach((entry, id) => {
      const shouldLock = locksSet.has(entry.name.toLowerCase())
      const isLocked = entry.poolStatus === 'locked'
      if (shouldLock !== isLocked) {
        next.set(id, { ...entry, poolStatus: (shouldLock ? 'locked' : 'included') as PoolStatus })
        changed = true
      }
    })
    if (changed) setPlayerPool(next)
  }

  // Slate auto-load
  const { slates, selectedSlate, setSelectedId, slateFile, loading: slateLoading } = useLatestSlate()
  useEffect(() => {
    if (slateFile) {
      setFile(slateFile)
      setValidationStatus(`Auto-loaded from saved slate (${slateFile.name})`)
      setError(null)
      // Parse auto-loaded slate into player pool, then auto-exclude OUT players
      parseCSVToPool(slateFile).then(async map => {
        const enriched = await applyInjuryExclusions(map)
        setPlayerPool(enriched)
      }).catch(() => {})
    }
  }, [slateFile, applyInjuryExclusions])

  // Faded teams + excluded players — read from dashboard localStorage
  const [fadedTeams, setFadedTeams]       = useState<string[]>([])
  const [excludedPlayers, setExcludedPlayers] = useState<string[]>([])

  useEffect(() => {
    try {
      const saved = localStorage.getItem('dfs_excluded_teams')
      if (saved) setFadedTeams(JSON.parse(saved) as string[])
    } catch { /* ignore */ }
    try {
      const saved = localStorage.getItem('dfs_excluded_players')
      if (saved) setExcludedPlayers(JSON.parse(saved) as string[])
    } catch { /* ignore */ }
  }, [])

  // Reset salary floor + pool when platform switches
  useEffect(() => {
    setMinSalary(SALARY_DEFAULTS[site])
    setResult(null)
    setError(null)
    setPlayerPool(new Map())
  }, [site])

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0]
    setError(null)
    setValidationStatus(null)
    setFile(null)
    setResult(null)
    setRunParams(null)
    setTaskId(null)

    if (!selected) return

    setValidating(true)

    try {
      const validation = await validateCSV(selected)

      if (!validation.valid) {
        setError({
          message: validation.error || 'Invalid CSV file',
          raw: validation.missingColumns
            ? `Missing: ${validation.missingColumns.join(', ')}\nFound: ${validation.foundColumns?.join(', ') || 'none'}`
            : undefined,
        })
        return
      }

      setFile(selected)
      setValidationStatus(`Valid CSV with ${validation.foundColumns?.length || 0} required columns`)
      setPlayerPool(new Map()) // reset before parsing — edge: avoids stale state if parse is slow
      parseCSVToPool(selected).then(async map => {
        const enriched = await applyInjuryExclusions(map)
        setPlayerPool(enriched)
      }).catch(() => {})
    } finally {
      setValidating(false)
    }
  }

  const handleRunOptimizer = async () => {
    if (!file) {
      setError({ message: 'Please select a valid CSV file first' })
      return
    }

    // ── Player pool validation ─────────────────────────────────────────────
    if (playerPool.size > 0) {
      const poolCheck = validatePool(playerPool, site)
      if (!poolCheck.ok) {
        setError({ message: poolCheck.errors[0] ?? 'Player pool validation failed.' })
        return
      }
    }

    setLoading(true)
    setError(null)
    setPlanGated(false)
    setResult(null)
    setTaskId(null)

    // Capture run parameters for display
    const params: RunParams = {
      numLineups,
      minSalary,
      maxSalary: SALARY_CAPS[site],
      maxExposure: maxExposure / 100,
      numUnique,
      platform: site === 'FD' ? 'FanDuel' : 'DraftKings',
      contestType: contestMode,
      site,
    }

    // ── Derive pool payload ────────────────────────────────────────────────
    const poolExcludes   = playerPool.size > 0 ? getPoolExcludedNames(playerPool) : []
    const projOverrides  = playerPool.size > 0 ? getPoolProjectionOverrides(playerPool) : {}
    const mergedExcludes = Array.from(new Set([...excludedPlayers, ...poolExcludes]))

    try {
      const normalizedFile = await normalizeCSVHeaders(file)

      // ── Primary path: async via Celery queue ──────────────────────────────
      const asyncResp = await runOptimizerAsync(normalizedFile, {
        numLineups: params.numLineups,
        maxExposure: params.maxExposure,
        numUnique,
        site: params.site,
        contestType: params.contestType,
        outTeams: fadedTeams.length > 0 ? fadedTeams : undefined,
        outPlayers: mergedExcludes.length > 0 ? mergedExcludes : undefined,
        chalkThreshold: chalkThreshold > 0 ? chalkThreshold : undefined,
        projectionOverrides: Object.keys(projOverrides).length > 0 ? projOverrides : undefined,
        lockedPlayers: poolLockNames.length > 0 ? poolLockNames : undefined,
      })

      if (asyncResp.success && asyncResp.taskId) {
        // Task queued — useTaskStatus polling takes over from here
        setRunParams(params)
        setTaskId(asyncResp.taskId)
        return
      }

      if (asyncResp.planGated) { setPlanGated(true); return }

      // ── Fallback: sync run when Celery / Redis is not available (503) ─────
      const syncResp = await runFDOptimizer(normalizedFile, {
        numLineups: params.numLineups,
        minSalary: params.minSalary,
        maxSalary: params.maxSalary,
        maxExposure: params.maxExposure,
        numUnique,
        site: params.site,
        outTeams: fadedTeams.length > 0 ? fadedTeams : undefined,
        outPlayers: mergedExcludes.length > 0 ? mergedExcludes : undefined,
        chalkThreshold: chalkThreshold > 0 ? chalkThreshold : undefined,
        projectionOverrides: Object.keys(projOverrides).length > 0 ? projOverrides : undefined,
        lockedPlayers: poolLockNames.length > 0 ? poolLockNames : undefined,
      })

      if (!syncResp.success) {
        if (syncResp.planGated) { setPlanGated(true); return }
        setError({
          message: 'Optimization failed. Check your CSV format and try again.',
          raw: syncResp.error,
        })
        return
      }

      if (syncResp.data) {
        setResult(syncResp.data)
        setRunParams(params)
      }
    } catch (err) {
      setError({
        message: 'An unexpected error occurred while optimizing.',
        raw: err instanceof Error ? err.message : String(err),
      })
    } finally {
      setLoading(false)
    }
  }

  const handleRunFullPipeline = async () => {
    if (!file) {
      setError({ message: 'Please select a valid CSV file first' })
      return
    }

    setPipelineLoading(true)
    setPipelineSteps(null)
    setError(null)
    setPlanGated(false)
    setResult(null)

    // Player pool validation
    if (playerPool.size > 0) {
      const poolCheck = validatePool(playerPool, site)
      if (!poolCheck.ok) {
        setError({ message: poolCheck.errors[0] ?? 'Player pool validation failed.' })
        setPipelineLoading(false)
        return
      }
    }

    try {
      // Pass raw slate CSV — the pipeline will NOT use DK/FD stock projections
      // ── Derive pool payload for full pipeline ──────────────────────────────
      const _poolExcludes   = playerPool.size > 0 ? getPoolExcludedNames(playerPool) : []
      const _projOverrides  = playerPool.size > 0 ? getPoolProjectionOverrides(playerPool) : {}
      const _mergedExcludes = Array.from(new Set([...excludedPlayers, ..._poolExcludes]))

      const response = await runFullPipeline(file, {
        site,
        numLineups,
        maxExposure: maxExposure / 100,
        numUnique,
        contestType: contestMode,
        outTeams: fadedTeams.length > 0 ? fadedTeams : undefined,
        outPlayers: _mergedExcludes.length > 0 ? _mergedExcludes : undefined,
        enableStacking,
        minGameStack,
        bringBackCount,
        refreshProps: true,
        chalkThreshold: chalkThreshold > 0 ? chalkThreshold : undefined,
        projectionOverrides: Object.keys(_projOverrides).length > 0 ? _projOverrides : undefined,
        lockedPlayers: poolLockNames.length > 0 ? poolLockNames : undefined,
      })

      if (!response.success) {
        if (response.planGated) { setPlanGated(true); return }
        setError({
          message: 'Full pipeline failed. Check your CSV format and try again.',
          raw: response.error,
        })
        return
      }

      if (response.data) {
        if (response.data.pipeline_steps) {
          setPipelineSteps(response.data.pipeline_steps)
        }
        setResult(response.data)
        setRunParams({
          numLineups,
          minSalary,
          maxSalary: SALARY_CAPS[site],
          maxExposure: maxExposure / 100,
          numUnique,
          platform: site === 'FD' ? 'FanDuel' : 'DraftKings',
          contestType: contestMode,
          site,
        })
      }
    } catch (err) {
      setError({
        message: 'An unexpected error occurred.',
        raw: err instanceof Error ? err.message : String(err),
      })
    } finally {
      setPipelineLoading(false)
    }
  }

  const handleDownloadCSV = async () => {
    if (!result?.download_file) {
      setError({ message: 'No file available for download' })
      return
    }

    try {
      const response = await downloadLineups(result.download_file)
      if (response.success && response.data) {
        downloadFile(response.data, result.download_file)
      } else {
        setError({
          message: 'Download failed. Please try again.',
          raw: response.error,
        })
      }
    } catch (err) {
      setError({
        message: 'Download failed unexpectedly.',
        raw: err instanceof Error ? err.message : String(err),
      })
    }
  }

  const lblCls = 'block text-[11px] text-text-muted uppercase mb-1 font-semibold'
  const inpCls = 'w-full bg-surface-overlay text-text-primary border border-surface-border rounded-md px-2.5 py-1.5 text-sm outline-none focus:border-primary'
  const thCls  = 'px-2.5 py-2 text-left border-b-2 border-surface-border text-text-muted text-[11px] font-bold uppercase whitespace-nowrap select-none'
  const tdCls  = 'px-2.5 py-1.5 text-text-secondary border-b border-surface-border/50 text-sm'

  return (
    <div className="min-h-[calc(100vh-48px)] bg-surface-base">
      <div className="max-w-[1100px] mx-auto px-6 py-6">

        {/* Header */}
        <div className="mb-5">
          <h1 className="m-0 text-2xl font-extrabold text-text-primary">
            {site === 'FD' ? 'FanDuel' : 'DraftKings'} NBA Optimizer
          </h1>
          <p className="mt-1 mb-0 text-sm text-text-muted">
            Upload a projections CSV and generate optimized lineups.
          </p>
        </div>

        {/* Faded teams banner */}
        {fadedTeams.length > 0 && (
          <div className="mb-4 px-4 py-2.5 bg-danger-muted border border-danger/40 rounded-lg flex items-center justify-between flex-wrap gap-2 text-xs text-danger">
            <span>
              <strong>{fadedTeams.length}</strong> team{fadedTeams.length !== 1 ? 's' : ''} faded from dashboard:{' '}
              <strong>{fadedTeams.join(', ')}</strong> — these players will be excluded from lineups.
            </span>
            <button
              onClick={() => { localStorage.setItem('dfs_excluded_teams', '[]'); setFadedTeams([]) }}
              className="px-2.5 py-1 rounded text-[11px] font-bold cursor-pointer bg-danger text-white border-0 hover:opacity-80 transition-opacity"
            >
              Clear fades
            </button>
          </div>
        )}

        {/* Excluded players banner */}
        {excludedPlayers.length > 0 && (
          <div className="mb-4 px-4 py-2.5 bg-warning-muted border border-warning/40 rounded-lg flex items-center justify-between flex-wrap gap-2 text-xs text-warning">
            <span>
              <strong>{excludedPlayers.length}</strong> player{excludedPlayers.length !== 1 ? 's' : ''} skipped from dashboard:{' '}
              <strong>{excludedPlayers.slice(0, 5).join(', ')}{excludedPlayers.length > 5 ? `, +${excludedPlayers.length - 5} more` : ''}</strong>
            </span>
            <button
              onClick={() => { localStorage.setItem('dfs_excluded_players', '[]'); setExcludedPlayers([]) }}
              className="px-2.5 py-1 rounded text-[11px] font-bold cursor-pointer bg-warning text-surface-base border-0 hover:opacity-80 transition-opacity"
            >
              Clear skips
            </button>
          </div>
        )}

        {/* Slate selector */}
        <SlateSelector
          slates={slates}
          selected={selectedSlate}
          loading={slateLoading}
          onSelect={setSelectedId}
          onFileOverride={f => { setFile(f); setValidationStatus(`Using override file: ${f.name}`); setError(null) }}
        />

        {/* Input card */}
        <div className="bg-surface-raised border border-surface-border rounded-xl mb-5 overflow-hidden">
          <div className="px-5 py-3 border-b border-surface-border text-sm font-bold text-text-primary">Upload Projections</div>
          <div className="px-5 py-4">

            {/* Contest Mode Selector */}
            <div className="mb-4">
              <ContestModeSelector
                value={contestMode}
                onChange={(mode: ContestMode, cfg: ContestModeConfig) => {
                  setContestMode(mode)
                  if (mode !== 'custom') {
                    setNumLineups(cfg.defaults.n_lineups)
                    setEnableStacking(cfg.defaults.enable_stacking)
                    setMinGameStack(cfg.defaults.min_game_stack)
                    setBringBackCount(cfg.defaults.bring_back_count)
                  }
                }}
              />
            </div>

            {/* Site Selector */}
            <div className="flex items-center gap-2 mb-4">
              <span className="text-xs text-text-muted">Platform:</span>
              {(['FD', 'DK'] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => setSite(s)}
                  className={cn(
                    'px-3.5 py-1.5 rounded text-sm font-bold cursor-pointer border-0 transition-colors',
                    site === s ? 'bg-primary text-white' : 'bg-surface-overlay text-text-secondary hover:text-text-primary',
                  )}
                >
                  {s === 'FD' ? 'FanDuel' : 'DraftKings'}
                </button>
              ))}
              <span className="text-[11px] text-text-muted ml-1">
                Cap: ${site === 'FD' ? '60,000' : '50,000'} &bull; {SITE_SLOT_CONFIG[site].length} players
              </span>
            </div>

            {/* File / Lineups / Salary row */}
            <div className="grid grid-cols-3 gap-3.5 mb-3.5">
              <div>
                <label className={lblCls}>CSV File <span className="text-text-muted">(max {MAX_FILE_SIZE_MB}MB)</span></label>
                {/* Hidden real input — triggered by the styled zone below */}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".csv"
                  className="hidden"
                  onChange={handleFileChange}
                  disabled={validating}
                />
                {/* Styled click/drag target */}
                <div
                  role="button"
                  tabIndex={0}
                  aria-label="Upload CSV file"
                  onClick={() => !validating && fileInputRef.current?.click()}
                  onKeyDown={e => (e.key === 'Enter' || e.key === ' ') && !validating && fileInputRef.current?.click()}
                  onDragOver={e => { e.preventDefault(); e.stopPropagation() }}
                  onDrop={e => {
                    e.preventDefault()
                    const dropped = e.dataTransfer.files?.[0]
                    if (dropped) handleFileChange({ target: { files: e.dataTransfer.files } } as React.ChangeEvent<HTMLInputElement>)
                  }}
                  className={cn(
                    'flex items-center gap-2 border-2 border-dashed rounded-lg px-3 py-2.5 text-sm cursor-pointer transition-colors select-none',
                    validating
                      ? 'border-warning/50 text-warning cursor-not-allowed'
                      : file
                        ? 'border-success/60 text-success bg-success-muted/30 hover:border-success'
                        : 'border-surface-border text-text-muted hover:border-primary hover:text-text-primary',
                  )}
                >
                  <svg className="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                  </svg>
                  <span className="truncate">
                    {validating ? 'Validating…' : file ? file.name : 'Click or drag CSV here'}
                  </span>
                </div>
                {file && validationStatus && !validating && (
                  <p className="text-[11px] text-success mt-1">{validationStatus}</p>
                )}
              </div>
              <div>
                <label className={lblCls}>Number of Lineups</label>
                <input type="number" min={1} max={150} value={numLineups}
                  onChange={e => setNumLineups(Number(e.target.value))} className={inpCls} />
              </div>
              <div>
                <label className={lblCls}>Minimum Salary</label>
                <input type="number" min={0} max={SALARY_CAPS[site]} step={1000} value={minSalary}
                  onChange={e => setMinSalary(Number(e.target.value))} className={inpCls} />
              </div>
            </div>

            {/* Stacking settings (shown only when enabled) */}
            {enableStacking && (
              <div className="grid grid-cols-3 gap-3.5 mb-3.5 px-3.5 py-3 bg-surface-base rounded-lg border border-primary-muted">
                <div>
                  <label className={lblCls}>Min Players / Game</label>
                  <input type="number" min={0} max={6} value={minGameStack}
                    onChange={e => setMinGameStack(Number(e.target.value))} className={inpCls} />
                </div>
                <div>
                  <label className={lblCls}>Bring-Back Count</label>
                  <input type="number" min={0} max={4} value={bringBackCount}
                    onChange={e => setBringBackCount(Number(e.target.value))} className={inpCls} />
                </div>
                <div className="flex items-center gap-2 mt-[18px]">
                  <input type="checkbox" checked={enableStacking} onChange={e => setEnableStacking(e.target.checked)}
                    id="stacking-toggle" className="accent-primary cursor-pointer" />
                  <label htmlFor="stacking-toggle" className="text-sm text-text-secondary cursor-pointer">
                    Enable Stacking
                  </label>
                </div>
              </div>
            )}

            {/* Chalk auto-fade */}
            <div className={cn(
              'mb-3.5 px-3.5 py-3 bg-surface-base rounded-lg border transition-colors',
              chalkThreshold > 0 ? 'border-purple-600/50' : 'border-surface-border',
            )}>
              <label className={lblCls}>
                Auto-Fade Chalk&nbsp;
                <span className={cn('font-bold', chalkThreshold > 0 ? 'text-purple-400' : 'text-text-muted')}>
                  {chalkThreshold > 0 ? `> ${chalkThreshold}% projected owned` : 'Off'}
                </span>
              </label>
              <div className="flex items-center gap-2.5 mt-1.5">
                <input
                  type="range" min={0} max={60} step={5}
                  value={chalkThreshold}
                  onChange={e => setChalkThreshold(Number(e.target.value))}
                  className="flex-1 accent-purple-500"
                />
                <span className="text-xs text-text-secondary min-w-[30px] text-right">
                  {chalkThreshold === 0 ? 'Off' : `${chalkThreshold}%`}
                </span>
              </div>
              {chalkThreshold > 0 && (
                <p className="text-[11px] text-purple-400 mt-1">
                  Players projected at {chalkThreshold}%+ ownership will be excluded from the pool.
                </p>
              )}
            </div>

            {/* Lineup Diversity Controls */}
            <div className="mb-3.5 px-3.5 py-3 bg-surface-base rounded-lg border border-primary-muted">
              <p className="text-[11px] text-text-muted uppercase font-bold tracking-wide mt-0 mb-2.5">Lineup Diversity</p>

              {/* Max Exposure */}
              <div className="mb-3">
                <label className={cn(lblCls, 'flex justify-between')}>
                  <span>Max Exposure</span>
                  <span className="text-primary font-bold">{maxExposure}%</span>
                </label>
                <input
                  type="range" min={10} max={80} step={5}
                  value={maxExposure}
                  onChange={e => setMaxExposure(Number(e.target.value))}
                  className="w-full accent-primary mt-1.5"
                />
                <p className="text-[11px] text-text-muted mt-1 mb-0">
                  Max % of lineups any single player can appear in. Lower = more variety. (GPP: 25–40%)
                </p>
              </div>

              {/* Num Unique */}
              <div>
                <label className={cn(lblCls, 'flex justify-between')}>
                  <span>Unique Players Across Lineups</span>
                  <span className="text-success font-bold">{numUnique}</span>
                </label>
                <input
                  type="range" min={1} max={6} step={1}
                  value={numUnique}
                  onChange={e => setNumUnique(Number(e.target.value))}
                  className="w-full accent-emerald-500 mt-1.5"
                />
                <p className="text-[11px] text-text-muted mt-1 mb-0">
                  How many players must differ between each consecutive lineup. Higher = more variety. (GPP: 3–4)
                </p>
              </div>
            </div>

            {/* Lock / Fade control */}
            {playerPool.size > 0 && (
              <div className="mb-3.5">
                <LockFadeControl
                  players={Array.from(playerPool.values()).map(e => ({
                    name: e.name, pos: e.pos, salary: e.salary, proj: e.adjProj, team: e.team,
                  } as ProjectionPlayer))}
                  locks={poolLockNames}
                  fades={[]}
                  onLocksChange={handleLocksChange}
                  onFadesChange={() => {}}
                />
              </div>
            )}

            <details className="mb-3.5">
              <summary className="text-xs text-text-muted cursor-pointer">Required CSV columns</summary>
              <div className="mt-2 p-3 bg-surface-base rounded-lg">
                <p className="text-xs text-text-secondary mt-0 mb-1.5">Your CSV must include these columns (or common aliases):</p>
                <ul className="m-0 pl-4">
                  {REQUIRED_COLUMNS.map(col => (
                    <li key={col.name} className="text-xs text-text-muted mb-0.5">
                      <strong className="text-text-secondary">{col.name}</strong>{' '}({col.aliases.join(', ')})
                    </li>
                  ))}
                </ul>
              </div>
            </details>

            {/* Run buttons */}
            <div className="flex gap-2.5 items-center flex-wrap">
              <button
                onClick={handleRunOptimizer}
                disabled={loading || asyncLoading || pipelineLoading || !file || validating}
                className="px-5 py-2 rounded text-[15px] font-bold bg-primary text-white border-0 cursor-pointer disabled:opacity-50 hover:bg-primary-hover transition-colors"
              >
                {loading
                  ? 'Submitting…'
                  : asyncLoading
                    ? taskStatus.status === 'queued'
                      ? 'Queued…'
                      : 'Optimizing…'
                    : playerPool.size > 0
                      ? (() => {
                          const included = Array.from(playerPool.values()).filter(e => e.poolStatus === 'included').length
                          const excluded = playerPool.size - included
                          return excluded > 0
                            ? `Run Optimizer (${included} in pool, ${excluded} OUT)`
                            : `Run Optimizer (${included} in pool)`
                        })()
                      : 'Run Optimizer'
                }
              </button>
              <button
                onClick={handleRunFullPipeline}
                disabled={loading || asyncLoading || pipelineLoading || !file || validating}
                className="px-5 py-2 rounded text-[15px] font-bold bg-success text-white border-0 cursor-pointer disabled:opacity-60 hover:brightness-110 transition-all"
              >
                {pipelineLoading ? '⏳ Running Pipeline…' : '⚡ Run Full Pipeline'}
              </button>
              <span className="text-[11px] text-text-muted">
                ↑ injuries · props · projections · ownership · optimize · download in one click
              </span>
            </div>
          </div>
        </div>

        {/* Injury Alert Banner */}
        {injurySummary && (injurySummary.out_count > 0 || injurySummary.questionable_count > 0) && (
          <div className="mb-4">
            {injuryAutoExcluded > 0 && (
              <div className="bg-red-600/10 border border-red-600/20 rounded-lg px-4 py-2 mb-2 text-xs text-red-400 font-semibold">
                {injuryAutoExcluded} OUT player{injuryAutoExcluded !== 1 ? 's' : ''} auto-excluded from pool
              </div>
            )}
            <InjuryAlertBanner summary={injurySummary} />
          </div>
        )}

        {/* Player Pool */}
        {playerPool.size > 0 && (
          <PlayerPoolPanel
            poolMap={playerPool}
            onPoolMapChange={setPlayerPool}
            site={site}
            defaultOpen={true}
          />
        )}

        {/* Task progress banner — shown while async optimizer is queued or running */}
        {asyncLoading && (
          <div className="bg-surface-raised border border-primary/40 rounded-xl px-4 py-3 mb-4 flex items-center gap-3">
            <span
              className="w-5 h-5 border-2 border-primary border-t-transparent rounded-full animate-spin shrink-0"
              aria-hidden="true"
            />
            <div>
              <p className="text-sm font-semibold text-text-primary m-0">
                {taskStatus.status === 'queued' ? 'Optimizer queued…' : 'Generating lineups…'}
              </p>
              <p className="text-xs text-text-muted m-0">
                {taskStatus.status === 'queued'
                  ? 'Waiting for a background worker to pick up your job.'
                  : 'Running LP solve and exposure optimization in the background.'}
              </p>
            </div>
          </div>
        )}

        {/* Plan-gate upgrade banner */}
        {planGated && (
          <div className="bg-warning-muted border border-warning/40 rounded-lg px-4 py-4 mb-5 flex flex-col sm:flex-row sm:items-center gap-3">
            <div className="flex-1">
              <p className="text-sm font-bold text-warning mb-0.5">Pro plan required</p>
              <p className="text-xs text-text-secondary">The optimizer is a Pro feature. Upgrade to unlock unlimited lineup generation, exposure controls, and EV modeling.</p>
            </div>
            <a
              href="/billing"
              className="shrink-0 px-4 py-2 rounded bg-warning text-white text-sm font-bold hover:bg-warning/90 transition-colors text-center no-underline"
            >
              Upgrade to Pro
            </a>
          </div>
        )}

        {/* Error */}
        {error && !planGated && <ErrorDisplay message={error.message} rawError={error.raw} />}

        {/* Skeleton while optimizer is running */}
        {(loading || asyncLoading || pipelineLoading) && !result && (
          <div className="bg-surface-raised border border-surface-border rounded-xl p-4">
            <SkeletonTable rows={10} cols={7} />
          </div>
        )}

        {/* Pipeline steps banner */}
        {pipelineSteps && (
          <div className="bg-success-muted border border-success/40 rounded-xl px-4 py-3 mb-4 text-xs text-success">
            <p className="mt-0 mb-2 font-bold text-sm text-success">⚡ Full Pipeline Completed</p>
            <div className="flex flex-wrap gap-2">
              {[
                ['📁 Slate', pipelineSteps.slate_uploaded],
                ['🩹 Injuries', pipelineSteps.injuries_refreshed ? '✓ refreshed' : '—'],
                ['📊 Props', (pipelineSteps.props_refreshed as Record<string,unknown>)?.status === 'updated'
                  ? `✓ ${(pipelineSteps.props_refreshed as Record<string,unknown>).players_covered} players`
                  : String((pipelineSteps.props_refreshed as Record<string,unknown>)?.status ?? '—')],
                ['🎯 Projected', `${pipelineSteps.projections_generated} players`],
                ['📈 Ownership', pipelineSteps.ownership_model],
                ['🏀 Lineups', `${pipelineSteps.lineups_generated}`],
              ].map(([label, val]) => (
                <span key={label} className="px-2.5 py-1 bg-surface-overlay border border-success/30 rounded text-[11px]">
                  <span className="text-success/70">{label}:</span>{' '}
                  <strong className="text-text-primary">{val}</strong>
                </span>
              ))}
            </div>
            <p className="mt-2 mb-0 text-[11px] text-success/60">
              Projection source: L10 game-log rolling avg + DvP. DK/FD stock averages never used.
            </p>
          </div>
        )}

        {/* Empty state — no optimizer run yet */}
        {!loading && !asyncLoading && !pipelineLoading && !error && result === null && (
          <div className="bg-surface-raised border border-surface-border rounded-xl mt-5">
            <EmptyState
              icon="🏆"
              title="No lineups generated yet"
              description="Upload a projections CSV above and click Generate Lineups to build your optimal lineup set."
            />
          </div>
        )}

        {/* Results */}
        {result && (
          <>
            {/* Stats panel */}
            <div className="bg-surface-raised border border-surface-border rounded-xl mb-4 overflow-hidden">
              <div className="px-5 py-3 border-b border-surface-border flex justify-between items-center">
                <span className="text-sm font-bold text-text-primary">Results</span>
                <button
                  onClick={handleDownloadCSV}
                  className="px-4 py-1.5 rounded text-sm font-semibold bg-success-muted text-success border border-success/40 hover:border-success/70 cursor-pointer transition-colors"
                >
                  Download CSV
                </button>
              </div>
              <div className="px-5 py-3.5">
                {runParams && <RunParametersDisplay params={runParams} />}
                <div className="grid grid-cols-4 gap-3">
                  <div className="bg-surface-base border border-surface-border rounded-lg px-4 py-3">
                    <p className="text-[11px] text-text-muted uppercase font-bold m-0 mb-1">Total Lineups</p>
                    <p className="text-2xl font-extrabold text-text-primary m-0">{result.total_lineups}</p>
                  </div>
                  <div className="bg-surface-base border border-surface-border rounded-lg px-4 py-3">
                    <p className="text-[11px] text-text-muted uppercase font-bold m-0 mb-1">Avg Projection</p>
                    <p className="text-2xl font-extrabold text-primary m-0">{formatProjection(result.stats?.avg_projection ?? 0)}</p>
                  </div>
                  <div className="bg-surface-base border border-surface-border rounded-lg px-4 py-3">
                    <p className="text-[11px] text-text-muted uppercase font-bold m-0 mb-1">Avg Salary</p>
                    <p className="text-2xl font-extrabold text-text-primary m-0">{formatSalary(result.stats?.avg_salary ?? 0)}</p>
                  </div>
                  <div className="bg-surface-base border border-surface-border rounded-lg px-4 py-3">
                    <p className="text-[11px] text-text-muted uppercase font-bold m-0 mb-1">Proj Range</p>
                    <p className="text-2xl font-extrabold text-text-primary m-0">{result.stats?.projection_range ?? 'N/A'}</p>
                  </div>
                </div>
                {result.message && (
                  <p className="mt-3 mb-0 text-success text-sm">{result.message}</p>
                )}
              </div>
            </div>

            {/* Lineups table */}
            <div className="bg-surface-raised border border-surface-border rounded-xl overflow-hidden">
              <div className="px-5 py-3 border-b border-surface-border text-sm font-bold text-text-primary">
                Lineup Preview (Top {result.lineups?.length ?? 0})
              </div>
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr>
                      <th className={thCls}>#</th>
                      {SITE_SLOT_CONFIG[site].map(slot => (
                        <th key={slot} className={thCls}>{slot.replace('_2', '')}</th>
                      ))}
                      <th className={cn(thCls, 'text-right')}>Salary</th>
                      <th className={cn(thCls, 'text-right')}>Proj</th>
                      <th className={cn(thCls, 'text-right')}>Own%</th>
                      <th className={thCls}></th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.lineups?.map((lineup: FDLineup, i: number) => (
                      <tr
                        key={lineup.lineup_num}
                        className={i % 2 === 0 ? 'bg-surface-raised' : 'bg-surface-base'}
                      >
                        <td className={tdCls}>{lineup.lineup_num}</td>
                        {SITE_SLOT_CONFIG[site].map(slot => (
                          <td key={slot} className={tdCls}>
                            {cleanName(lineup[slot as SlotKey])}
                          </td>
                        ))}
                        <td className={cn(tdCls, 'text-right')}>{formatSalary(lineup.total_salary)}</td>
                        <td className={cn(tdCls, 'text-right text-success font-bold')}>
                          {formatProjection(lineup.projected_points)}
                        </td>
                        <td className={cn(
                          tdCls, 'text-right font-mono',
                          lineup.total_ownership != null
                            ? (lineup.total_ownership < 150 ? 'text-success' : lineup.total_ownership < 200 ? 'text-warning' : 'text-danger')
                            : 'text-text-muted',
                        )}>
                          {lineup.total_ownership != null ? `${lineup.total_ownership.toFixed(1)}%` : '—'}
                        </td>
                        <td className={cn(tdCls, 'text-right')}>
                          <CopyLineupButton
                            players={SITE_SLOT_CONFIG[site]
                              .map(slot => lineup[slot as SlotKey] as string)
                              .filter(Boolean)}
                            lineupIndex={i}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
