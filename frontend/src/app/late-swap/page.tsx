'use client'

import { useState, useMemo, useCallback } from 'react'
import {
  batchLateSwap,
  downloadFile,
  type BatchLateSwapResponse,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import {
  importFanDuelEntriesSelfContained,
  readFileAsText,
} from '@/lib/late-swap/import/importFanDuelEntries'
import {
  importDraftKingsEntries,
  detectCSVSite,
} from '@/lib/late-swap/import/importDraftKingsEntries'
import {
  exportFanDuelLineups,
  downloadCSV,
} from '@/lib/late-swap/export/exportFanDuelLineups'
import { exportDraftKingsLineups } from '@/lib/late-swap/export/exportDraftKingsLineups'
import {
  validateFDLineups,
  hasBlockingValidationErrors,
  blockingErrorSummary,
} from '@/lib/late-swap/validation/validateFDLineup'
import { validateExportReadiness } from '@/lib/late-swap/diagnostics'
import { FailureReason } from '@/lib/late-swap/types'
import type { LineupSwapComparison } from '@/lib/late-swap/types'
import type { FDLineupEntry } from '@/lib/late-swap/models/fdLineup'
import type { DKLineupEntry } from '@/lib/late-swap/models/dkLineup'
import { BatchSwapResultsTable } from '@/components/lateSwap/BatchSwapResultsTable'
import {
  PlayerCommandCenter,
  type PlayerRec,
  type PlayerStatus,
} from '@/components/lateSwap/PlayerCommandCenter'
import { RunBar, type ContestType } from '@/components/lateSwap/RunBar'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Site = 'DK' | 'FD'
type Step = 'upload' | 'command' | 'results'

interface ManagedLineup {
  id: number
  label: string
  players: string[]
  originalPlayers: string[]
  slotLabels: string[]
  fdEntry?: FDLineupEntry
  dkEntry?: DKLineupEntry
}

function StatCard({
  label,
  value,
  tone = 'neutral',
}: {
  label: string
  value: string
  tone?: 'neutral' | 'gold' | 'blue' | 'danger'
}) {
  const toneClass =
    tone === 'gold'
      ? 'text-[#f4b540]'
      : tone === 'blue'
      ? 'text-[#7dd3fc]'
      : tone === 'danger'
      ? 'text-[#fda4af]'
      : 'text-text-primary'

  return (
    <div className="score-card rounded-2xl px-4 py-3 min-w-[140px] flex-1">
      <div className="section-label mb-2">{label}</div>
      <div className={cn('text-2xl font-black tracking-tight', toneClass)}>{value}</div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Upload drop-zone component
// ---------------------------------------------------------------------------

function UploadStep({
  loading,
  error,
  onFile,
}: {
  loading: boolean
  error: string | null
  onFile: (f: File) => void
}) {
  const [dragging, setDragging] = useState(false)

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files?.[0]
    if (f) onFile(f)
  }

  return (
    <div className="relative flex flex-col items-center justify-center min-h-[70vh] gap-8 px-4 sm:px-6 py-8 sm:py-10 overflow-hidden">
      <div className="absolute inset-0 pointer-events-none opacity-60">
        <div className="absolute left-[8%] top-[12%] h-44 w-44 rounded-full bg-[#f59e0b]/12 blur-3xl" />
        <div className="absolute right-[10%] top-[18%] h-56 w-56 rounded-full bg-[#38bdf8]/12 blur-3xl" />
        <div className="absolute inset-x-[15%] bottom-[6%] h-40 rounded-full bg-[#1d4ed8]/10 blur-3xl" />
      </div>

      <div className="relative text-center max-w-2xl">
        <div className="section-label mb-4 text-[#f4b540]">Late Swap Control Room</div>
        <h1 className="display-title text-4xl md:text-5xl font-black text-text-primary mb-3 tracking-tight leading-none">
          Broadcast-grade late swap for NBA DFS
        </h1>
        <p className="text-text-secondary text-base max-w-xl mx-auto leading-7">
          Drop your FanDuel or DraftKings entry template CSV to begin.
          The workflow keeps your current import, lock, scratch, and export behavior intact while making the interface easier to scan under time pressure.
        </p>
      </div>

      <label
        onDrop={handleDrop}
        onDragOver={e => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        className={cn(
          'glass-panel-strong gold-ring relative w-full max-w-3xl cursor-pointer rounded-[28px] border px-6 sm:px-10 py-12 sm:py-16 text-center transition-all overflow-hidden',
          dragging
            ? 'scale-[1.02] border-primary'
            : 'hover:border-primary/40 hover:-translate-y-0.5',
        )}
      >
        <div className="absolute inset-0 pointer-events-none bg-[radial-gradient(circle_at_top,rgba(244,181,64,0.14),transparent_36%),radial-gradient(circle_at_bottom_right,rgba(56,189,248,0.12),transparent_30%)]" />
        <input
          type="file"
          accept=".csv"
          className="hidden"
          onChange={e => e.target.files?.[0] && onFile(e.target.files[0])}
        />
        {loading ? (
          <div className="relative flex flex-col items-center gap-3">
            <span className="inline-block w-8 h-8 border-4 border-primary/30 border-t-primary rounded-full animate-spin" />
            <span className="text-sm text-text-muted">Parsing lineups…</span>
          </div>
        ) : (
          <>
            <div className="relative text-6xl mb-5 opacity-70">🏀</div>
            <div className="section-label mb-3 text-[#f4b540]">Upload Center</div>
            <div className="text-xl font-black text-text-primary mb-2 tracking-tight">
              Drop entry template here
            </div>
            <div className="text-sm text-text-secondary">
              or click to browse · FD &amp; DK supported · auto-detects site
            </div>
          </>
        )}
      </label>

      <div className="relative grid w-full max-w-5xl grid-cols-1 md:grid-cols-3 gap-4">
        <StatCard label="Speed" value="Import to run in seconds" tone="gold" />
        <StatCard label="Control" value="Manual lock / scratch overrides" tone="blue" />
        <StatCard label="Output" value="CSV-safe export pipeline" />
      </div>

      {error && (
        <div className="glass-strip w-full max-w-3xl rounded-2xl px-4 py-3 text-sm text-[#fecdd3] border-danger/30">
          ⚠ {error}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Salary cap by site
// ---------------------------------------------------------------------------
const SALARY_CAP: Record<Site, number> = { FD: 60000, DK: 50000 }

// Game-time warning string emitted by the FD importer when dates can't parse
const GAME_TIME_WARN =
  'Could not infer FanDuel game start times from this entry template — started-player auto-lock did not run. Use the Player Controls to manually mark out/scratched players (❌) before running Batch Swap.'

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function LateSwapPage() {
  // ── Core state ─────────────────────────────────────────────────────────────
  const [step, setStep] = useState<Step>('upload')
  const [site, setSite] = useState<Site>('DK')
  const [slateFile, setSlateFile] = useState<File | null>(null)
  const [entryFileName, setEntryFileName] = useState<string | null>(null)

  const [managedLineups, setManagedLineups] = useState<ManagedLineup[]>([])

  /**
   * Per-player status keyed by player name lowercased.
   * This is the single source of truth for lock / scratch / exclude state.
   */
  const [statusMap, setStatusMap] = useState<Map<string, PlayerStatus>>(new Map())

  /**
   * Players whose games have started — set once at import time and never
   * mutated. Drives the orange "STARTED" badge even after the user changes
   * the player's status manually.
   */
  const [gameStartedSet, setGameStartedSet] = useState<Set<string>>(new Set())

  /** FD compositeId → game text (e.g. "ATL@NYK 7:30PM ET") */
  const [playerMatchups, setPlayerMatchups] = useState<Map<string, string>>(new Map())
  /** FD player name (any casing) → compositeId */
  const [fdNameToId, setFdNameToId] = useState<Map<string, string>>(new Map())
  /** FD compositeId → canonical display name */
  const [fdIdToName, setFdIdToName] = useState<Map<string, string>>(new Map())
  /** DK player name lowercased → DK player ID */
  const [dkNameToId, setDkNameToId] = useState<Map<string, string>>(new Map())

  // Run settings
  const [contestType, setContestType] = useState<ContestType>('balanced')
  const [wProj, setWProj] = useState(0.5)
  const [wValue, setWValue] = useState(0.3)
  const [wOwn, setWOwn] = useState(0.2)
  const [diversityFactor, setDiversityFactor] = useState(0.3)

  // Import UI state
  const [importLoading, setImportLoading] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)
  const [importWarnings, setImportWarnings] = useState<string[]>([])

  // Batch swap state
  const [batchLoading, setBatchLoading] = useState(false)
  const [batchResult, setBatchResult] = useState<BatchLateSwapResponse | null>(null)
  const [batchError, setBatchError] = useState<string | null>(null)
  const [swapComparisons, setSwapComparisons] = useState<Map<number, LineupSwapComparison>>(
    new Map(),
  )

  // Results navigation
  const [activeLineupIdx, setActiveLineupIdx] = useState(0)

  // ── Derived: unique player roster records ──────────────────────────────────
  const playerRecs: PlayerRec[] = useMemo(() => {
    const map = new Map<string, PlayerRec>()
    const total = managedLineups.length
    for (const lu of managedLineups) {
      for (const name of lu.players) {
        if (!name) continue
        const key = name.toLowerCase()
        const existing = map.get(key)
        if (existing) {
          existing.lineupCount++
          existing.totalLineups = total
        } else {
          const fdId = fdNameToId.get(name) ?? fdNameToId.get(key) ?? ''
          const gameText = fdId ? (playerMatchups.get(fdId) ?? '') : ''
          map.set(key, {
            name,
            gameText,
            gameStarted: gameStartedSet.has(key),
            lineupCount: 1,
            totalLineups: total,
          })
        }
      }
    }
    return [...map.values()].sort((a, b) => b.lineupCount - a.lineupCount)
  }, [managedLineups, playerMatchups, fdNameToId, gameStartedSet])

  // ── Derived: scratch / lock name arrays ───────────────────────────────────
  /**
   * Return the canonical (display) name for a lowercase key, falling back
   * to whatever the key is if no match found.
   */
  const canonicalName = useCallback(
    (key: string) => playerRecs.find(p => p.name.toLowerCase() === key)?.name ?? key,
    [playerRecs],
  )

  const scratchedNames = useMemo(
    () =>
      [...statusMap.entries()]
        .filter(([, v]) => v === 'scratched')
        .map(([k]) => canonicalName(k)),
    [statusMap, canonicalName],
  )

  const lockedNames = useMemo(
    () =>
      [...statusMap.entries()]
        .filter(([, v]) => v === 'locked')
        .map(([k]) => canonicalName(k)),
    [statusMap, canonicalName],
  )

  const scratchedSet = useMemo(
    () => new Set(scratchedNames.map(n => n.toLowerCase())),
    [scratchedNames],
  )

  const affectedCount = useMemo(
    () =>
      managedLineups.filter(lu =>
        lu.players.some(p => scratchedSet.has(p.toLowerCase())),
      ).length,
    [managedLineups, scratchedSet],
  )

  // ── Player status handlers ─────────────────────────────────────────────────
  const setPlayerStatus = useCallback((name: string, status: PlayerStatus) => {
    setStatusMap(prev => {
      const next = new Map(prev)
      const key = name.toLowerCase()
      if (status === null) next.delete(key)
      else next.set(key, status)
      return next
    })
  }, [])

  const handleBulkAction = useCallback(
    (action: 'lockAllStarted' | 'clearScratches' | 'clearLocks' | 'reset') => {
      setStatusMap(prev => {
        const next = new Map(prev)
        if (action === 'reset') {
          next.clear()
          return next
        }
        if (action === 'clearScratches') {
          for (const [k, v] of next) if (v === 'scratched') next.delete(k)
          return next
        }
        if (action === 'clearLocks') {
          for (const [k, v] of next) if (v === 'locked') next.delete(k)
          return next
        }
        // lockAllStarted
        for (const p of playerRecs) {
          if (p.gameStarted) next.set(p.name.toLowerCase(), 'locked')
        }
        return next
      })
    },
    [playerRecs],
  )

  // ── Import handler ─────────────────────────────────────────────────────────
  const handleImportCSV = useCallback(
    async (entryFile: File) => {
      setEntryFileName(entryFile.name)
      setImportLoading(true)
      setImportError(null)
      setImportWarnings([])
      setManagedLineups([])
      setStatusMap(new Map())
      setGameStartedSet(new Set())
      setBatchResult(null)
      setSwapComparisons(new Map())
      setSlateFile(null)

      try {
        const csvText = await readFileAsText(entryFile)
        const detectedSite = detectCSVSite(csvText)
        const effectiveSite = detectedSite ?? site
        if (detectedSite && detectedSite !== site) setSite(detectedSite)

        // ── FD path ──────────────────────────────────────────────────────────
        if (effectiveSite === 'FD') {
          const {
            lineups: fdEntries,
            warnings: rawWarnings,
            nameToIdMap,
            idToNameMap,
            lockedByGameTime: csvLockedNames,
            playerMatchups: pm,
            contestSlateDate,
          } = importFanDuelEntriesSelfContained(csvText)

          if (fdEntries.length === 0) {
            setImportError(
              rawWarnings.length > 0
                ? rawWarnings.slice(0, 3).join('; ')
                : 'No lineups found. Use the FD entry upload CSV template.',
            )
            setImportLoading(false)
            return
          }

          setFdNameToId(nameToIdMap)
          setFdIdToName(idToNameMap)
          setPlayerMatchups(pm)

          // Try to enrich game-start data via the backend /api/games/today
          let apiLockedNames: string[] = []
          let apiEnrichmentSucceeded = false
          const effectiveSlateDate = contestSlateDate ?? new Date()

          if (rawWarnings.includes(GAME_TIME_WARN) && pm.size > 0) {
            try {
              const apiBase =
                process.env.NEXT_PUBLIC_API_BASE ??
                process.env.NEXT_PUBLIC_API_URL ??
                'http://localhost:8000'
              const ctrl = new AbortController()
              const tid = setTimeout(() => ctrl.abort(), 3000)
              const gamesRes = await fetch(`${apiBase}/api/games/today`, {
                signal: ctrl.signal,
              })
              clearTimeout(tid)

              if (gamesRes.ok) {
                const gamesData = await gamesRes.json()
                const apiGames: Array<{
                  home_abbr: string
                  away_abbr: string
                  time: string
                }> = gamesData.games ?? []

                // Team abbreviation maps — FD uses short forms, API uses full
                const TO_CANON: Record<string, string> = {
                  GS: 'GSW', SA: 'SAS', PHO: 'PHX', NO: 'NOP', NY: 'NYK',
                }
                const FROM_CANON: Record<string, string> = {
                  GSW: 'GS', SAS: 'SA', PHX: 'PHO', NOP: 'NO', NYK: 'NY',
                }

                const parseApiGameTime = (timeStr: string): Date | null => {
                  const m = timeStr.match(/(\d{1,2}):(\d{2})\s*(AM|PM)\s+ET/i)
                  if (!m) return null
                  let hour = parseInt(m[1], 10)
                  const min = parseInt(m[2], 10)
                  if (m[3].toUpperCase() === 'PM' && hour !== 12) hour += 12
                  if (m[3].toUpperCase() === 'AM' && hour === 12) hour = 0
                  const y = effectiveSlateDate.getUTCFullYear()
                  const mo = effectiveSlateDate.getUTCMonth()
                  const d = effectiveSlateDate.getUTCDate()
                  // Build a UTC timestamp then correct for ET offset
                  const probe = new Date(Date.UTC(y, mo, d, hour + 5, min))
                  const fmt = new Intl.DateTimeFormat('en-US', {
                    timeZone: 'America/New_York',
                    year: 'numeric',
                    month: 'numeric',
                    day: 'numeric',
                    hour: 'numeric',
                    minute: 'numeric',
                    second: 'numeric',
                    hour12: false,
                  })
                  const parts = fmt.formatToParts(probe)
                  const get = (type: string) =>
                    parseInt(parts.find(p => p.type === type)?.value ?? '0', 10)
                  const etH = get('hour') === 24 ? 0 : get('hour')
                  const etMs = Date.UTC(
                    get('year'),
                    get('month') - 1,
                    get('day'),
                    etH,
                    get('minute'),
                    get('second'),
                  )
                  const offsetMin = (etMs - probe.getTime()) / 60000
                  return new Date(
                    Date.UTC(y, mo, d, hour, min) - offsetMin * 60000 + 5 * 60 * 1000,
                  )
                }

                if (apiGames.length > 0) {
                  apiEnrichmentSucceeded = true
                  const now = new Date(Date.now())
                  const startedMatchups = new Set<string>()

                  for (const game of apiGames) {
                    const gameTime = parseApiGameTime(game.time)
                    if (gameTime && gameTime <= now) {
                      const awayF = FROM_CANON[game.away_abbr] ?? game.away_abbr
                      const homeF = FROM_CANON[game.home_abbr] ?? game.home_abbr
                      startedMatchups.add(
                        `${game.away_abbr.toUpperCase()}@${game.home_abbr.toUpperCase()}`,
                      )
                      startedMatchups.add(`${awayF.toUpperCase()}@${homeF.toUpperCase()}`)
                    }
                  }

                  for (const [compositeId, matchup] of pm) {
                    const mu = matchup.toUpperCase()
                    const muCanon = mu
                      .split('@')
                      .map(a => TO_CANON[a] ?? FROM_CANON[a] ?? a)
                      .join('@')
                    if (startedMatchups.has(mu) || startedMatchups.has(muCanon)) {
                      const name = idToNameMap.get(compositeId)
                      if (name) apiLockedNames.push(name)
                    }
                  }
                }
              }
            } catch {
              /* enrichment unavailable — manual review warning will remain */
            }
          }

          // Merge auto-detected started names
          const allStartedNames = [...csvLockedNames]
          for (const name of apiLockedNames) {
            if (!allStartedNames.some(n => n.toLowerCase() === name.toLowerCase())) {
              allStartedNames.push(name)
            }
          }

          // Set game-started tracking and auto-lock status
          setGameStartedSet(new Set(allStartedNames.map(n => n.toLowerCase())))
          setStatusMap(() => {
            const m = new Map<string, PlayerStatus>()
            for (const name of allStartedNames) m.set(name.toLowerCase(), 'locked')
            return m
          })

          // Strip enrichment warning if API call succeeded
          const warnings = apiEnrichmentSucceeded
            ? rawWarnings.filter(w => w !== GAME_TIME_WARN)
            : rawWarnings
          setImportWarnings(warnings.slice(0, 5))

          const managed: ManagedLineup[] = fdEntries.map((entry, i) => ({
            id: i,
            label: `Lineup ${i + 1}`,
            players: entry.slots.map(s => s.playerName),
            originalPlayers: entry.slots.map(s => s.playerName),
            slotLabels: entry.slots.map(s => s.slot),
            fdEntry: entry,
          }))
          setManagedLineups(managed)
          setStep('command')
          return
        }

        // ── DK path ──────────────────────────────────────────────────────────
        const {
          lineups: dkEntries,
          warnings: dkWarnings,
          nameToIdMap,
          lockedByGameTime,
        } = importDraftKingsEntries(csvText)

        if (dkEntries.length === 0) {
          setImportError(
            dkWarnings.length > 0
              ? dkWarnings.slice(0, 3).join('; ')
              : 'No lineups found. Use the DraftKings entries export file.',
          )
          setImportLoading(false)
          return
        }

        setDkNameToId(nameToIdMap)
        setImportWarnings(dkWarnings.slice(0, 5))

        const startedLower = new Set(lockedByGameTime.map(n => n.toLowerCase()))
        setGameStartedSet(startedLower)
        setStatusMap(() => {
          const m = new Map<string, PlayerStatus>()
          for (const name of lockedByGameTime) m.set(name.toLowerCase(), 'locked')
          return m
        })

        const managed: ManagedLineup[] = dkEntries.map((entry, i) => ({
          id: i,
          label: `Lineup ${i + 1}`,
          players: entry.slots.map(s => s.playerName),
          originalPlayers: entry.slots.map(s => s.playerName),
          slotLabels: entry.slots.map(s => s.slot),
          dkEntry: entry,
        }))
        setManagedLineups(managed)
        setStep('command')
      } catch (err) {
        setImportError(err instanceof Error ? err.message : 'Import failed')
      } finally {
        setImportLoading(false)
      }
    },
    [site],
  )

  // ── Batch swap handler ─────────────────────────────────────────────────────
  const handleBatchSwap = useCallback(async () => {
    if (!slateFile || managedLineups.length === 0) return

    if (scratchedNames.length === 0) {
      setBatchError(
        'No scratched players — mark players as scratched (❌) in the Player Controls above, then run again.',
      )
      return
    }

    setBatchLoading(true)
    setBatchResult(null)
    setBatchError(null)
    setSwapComparisons(new Map())

    try {
      const res = await batchLateSwap(slateFile, {
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
        setBatchError(res.error ?? 'Batch swap failed — check the slate file and try again.')
        setBatchLoading(false)
        return
      }

      setBatchResult(res.data)

      // Build per-lineup comparisons and apply player updates
      const newComparisons = new Map<number, LineupSwapComparison>()
      const updatedLineups = managedLineups.map(lu => ({
        ...lu,
        players: [...lu.players],
        fdEntry: lu.fdEntry
          ? { ...lu.fdEntry, slots: lu.fdEntry.slots.map(s => ({ ...s })) }
          : undefined,
        dkEntry: lu.dkEntry
          ? { ...lu.dkEntry, slots: lu.dkEntry.slots.map(s => ({ ...s })) }
          : undefined,
      }))

      for (const log of res.data.swap_log) {
        const idx = log.lineup_index - 1 // backend is 1-indexed
        if (idx < 0 || idx >= managedLineups.length) continue

        const totalSalaryDelta = log.swaps.reduce((s, sw) => s + (sw.salary_delta ?? 0), 0)
        const totalProjDelta = log.swaps.reduce((s, sw) => s + (sw.proj_delta ?? 0), 0)

        newComparisons.set(idx, {
          lineupIdx: idx,
          diffs: log.swaps.map(sw => ({
            removed: sw.scratched,
            added: sw.replacement ?? null,
            projDelta: sw.proj_delta ?? 0,
            salaryDelta: sw.salary_delta ?? 0,
            success: sw.replacement !== null,
            ...(sw.replacement === null
              ? {
                  failureReason: FailureReason.NO_ELIGIBLE_REPLACEMENT,
                  failureMessage: sw.note ?? `No replacement found for ${sw.scratched}`,
                }
              : {}),
          })),
          finalSalary: log.total_salary,
          originalSalary: log.total_salary - totalSalaryDelta,
          salaryCap: log.salary_cap,
          totalProjDelta,
          totalSalaryDelta,
          successCount: log.swaps_made,
          failureCount: log.swaps_failed,
          source: 'batch',
          warnings: [],
        })

        // Apply swap names and update entry slot data
        if (log.swaps_made > 0) {
          for (const sw of log.swaps) {
            if (!sw.replacement) continue
            const lu = updatedLineups[idx]

            // Update player name array
            lu.players = lu.players.map(p =>
              p.toLowerCase() === sw.scratched.toLowerCase() ? sw.replacement! : p,
            )

            // Update FD entry slots
            if (lu.fdEntry) {
              const slotIdx = lu.fdEntry.slots.findIndex(
                s => s.playerName.toLowerCase() === sw.scratched.toLowerCase(),
              )
              if (slotIdx >= 0) {
                const compositeId =
                  fdNameToId.get(sw.replacement) ??
                  fdNameToId.get(sw.replacement.toLowerCase()) ??
                  ''
                lu.fdEntry = {
                  ...lu.fdEntry,
                  slots: lu.fdEntry.slots.map((s, i) =>
                    i === slotIdx
                      ? { ...s, fdPlayerId: compositeId, playerName: sw.replacement! }
                      : s,
                  ),
                }
              }
            }

            // Update DK entry slots
            if (lu.dkEntry) {
              const slotIdx = lu.dkEntry.slots.findIndex(
                s => s.playerName.toLowerCase() === sw.scratched.toLowerCase(),
              )
              if (slotIdx >= 0) {
                const playerId =
                  dkNameToId.get(sw.replacement.toLowerCase()) ??
                  dkNameToId.get(sw.replacement) ??
                  ''
                lu.dkEntry = {
                  ...lu.dkEntry,
                  slots: lu.dkEntry.slots.map((s, i) =>
                    i === slotIdx
                      ? { ...s, playerId, playerName: sw.replacement! }
                      : s,
                  ),
                }
              }
            }
          }
        }
      }

      setSwapComparisons(newComparisons)
      setManagedLineups(updatedLineups)
      setStep('results')
    } catch (err) {
      setBatchError(err instanceof Error ? err.message : 'Unknown error during batch swap')
    } finally {
      setBatchLoading(false)
    }
  }, [
    slateFile,
    managedLineups,
    scratchedNames,
    lockedNames,
    site,
    wProj,
    wValue,
    wOwn,
    contestType,
    diversityFactor,
    fdNameToId,
    dkNameToId,
  ])

  // ── Export handler ─────────────────────────────────────────────────────────
  const handleExport = useCallback(() => {
    if (managedLineups.length === 0) return

    const validationErrors = validateExportReadiness(managedLineups, scratchedSet)
    const blocking = validationErrors.filter(e => e.severity === 'error')
    if (blocking.length > 0) {
      setBatchError(
        `Export blocked — ${blocking.length} lineup${blocking.length > 1 ? 's' : ''} still contain scratched players: ${blocking.map(e => e.lineup).join(', ')}`,
      )
      return
    }

    if (site === 'FD') {
      const fdEntries = managedLineups
        .map(lu => lu.fdEntry)
        .filter((e): e is FDLineupEntry => e != null)
      if (fdEntries.length === 0) {
        setBatchError('No FanDuel entry data — re-import with an FD template CSV.')
        return
      }
      const fdValidations = validateFDLineups(fdEntries)
      if (hasBlockingValidationErrors(fdValidations)) {
        setBatchError(`FD lineup validation failed: ${blockingErrorSummary(fdValidations)}`)
        return
      }
      const csv = exportFanDuelLineups(fdEntries)
      downloadCSV(csv, `lineups_FD_late_swap_${new Date().toISOString().slice(0, 10)}.csv`)
      return
    }

    if (site === 'DK') {
      const dkEntries = managedLineups
        .map(lu => lu.dkEntry)
        .filter((e): e is DKLineupEntry => e != null)
      if (dkEntries.length > 0) {
        const csv = exportDraftKingsLineups(dkEntries)
        downloadCSV(csv, `lineups_DK_late_swap_${new Date().toISOString().slice(0, 10)}.csv`)
        return
      }
    }

    // Fallback: plain text dump
    const content = managedLineups
      .map(lu => `=== ${lu.label} ===\n${lu.players.join('\n')}`)
      .join('\n\n')
    downloadFile(content, `all_lineups_${site}_${new Date().toISOString().slice(0, 10)}.txt`)
  }, [managedLineups, scratchedSet, site])

  // ── Reset ──────────────────────────────────────────────────────────────────
  const resetSession = useCallback(() => {
    setStep('upload')
    setManagedLineups([])
    setStatusMap(new Map())
    setGameStartedSet(new Set())
    setPlayerMatchups(new Map())
    setFdNameToId(new Map())
    setFdIdToName(new Map())
    setDkNameToId(new Map())
    setSlateFile(null)
    setBatchResult(null)
    setSwapComparisons(new Map())
    setEntryFileName(null)
    setImportError(null)
    setImportWarnings([])
    setBatchError(null)
  }, [])

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-surface-base flex flex-col relative overflow-hidden">
      <div className="pointer-events-none absolute inset-0 opacity-80">
        <div className="absolute left-[-8rem] top-24 h-80 w-80 rounded-full bg-[#f59e0b]/10 blur-3xl" />
        <div className="absolute right-[-6rem] top-40 h-96 w-96 rounded-full bg-[#38bdf8]/10 blur-3xl" />
        <div className="absolute inset-x-0 top-0 h-56 bg-[linear-gradient(180deg,rgba(255,255,255,0.02),transparent)]" />
      </div>

      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <header className="glass-panel-strong relative flex items-center gap-3 px-4 sm:px-5 py-3.5 border-b border-surface-border flex-wrap shrink-0 mx-2 sm:mx-3 mt-2 sm:mt-3 rounded-2xl">
        <div className="flex items-center gap-3">
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-[#f4b540]/12 text-xl border border-[#f4b540]/20">🏀</span>
          <div>
            <div className="section-label text-[#f4b540] mb-1">Control Room</div>
            <span className="text-lg font-black text-text-primary tracking-tight">Late Swap</span>
          </div>
        </div>

        {entryFileName && (
          <span className="glass-strip text-[11px] text-text-secondary px-3 py-1 rounded-full font-mono truncate max-w-[260px]">
            {entryFileName}
          </span>
        )}

        {managedLineups.length > 0 && (
          <span
            className={cn(
              'text-[11px] font-bold px-3 py-1 rounded-full shrink-0 border',
              site === 'FD'
                ? 'bg-[#1a3552] text-[#7dd3fc] border-[#7dd3fc]/20'
                : 'bg-[#0f2a1a] text-[#86efac] border-[#86efac]/20',
            )}
          >
            {site}
          </span>
        )}

        {/* Breadcrumb */}
        {step !== 'upload' && (
          <nav className="flex items-center gap-1 text-[11px] ml-1 glass-strip rounded-full px-3 py-1 max-w-full overflow-x-auto scrollbar-thin">
            <button
              onClick={() => setStep('command')}
              disabled={managedLineups.length === 0}
              className={cn(
                'font-semibold cursor-pointer bg-transparent border-none p-0 transition-colors',
                step === 'command'
                  ? 'text-text-primary'
                  : 'text-text-muted hover:text-text-secondary',
              )}
            >
              Player Controls
            </button>
            {batchResult && (
              <>
                <span className="text-text-muted">›</span>
                <button
                  onClick={() => setStep('results')}
                  className={cn(
                    'font-semibold cursor-pointer bg-transparent border-none p-0 transition-colors',
                    step === 'results'
                      ? 'text-text-primary'
                      : 'text-text-muted hover:text-text-secondary',
                  )}
                >
                  Results
                </button>
              </>
            )}
          </nav>
        )}

        {step !== 'upload' && (
          <button
            onClick={resetSession}
            className="ml-auto text-[11px] font-semibold text-text-secondary hover:text-[#fda4af] cursor-pointer bg-transparent border border-surface-border/70 rounded-full px-3 py-1.5 transition-colors glass-strip"
          >
            ↺ New Session
          </button>
        )}
      </header>

      {/* ── Upload step ─────────────────────────────────────────────────────── */}
      {step === 'upload' && (
        <UploadStep loading={importLoading} error={importError} onFile={handleImportCSV} />
      )}

      {/* ── Command center / Results ─────────────────────────────────────────── */}
      {(step === 'command' || step === 'results') && (
        <div className="relative flex-1 flex flex-col overflow-hidden px-2 sm:px-3 pb-3">
          {/* Import warnings banner */}
          {importWarnings.length > 0 && (
            <div className="glass-strip flex items-start gap-2 px-5 py-3 border border-[#713f12]/50 rounded-2xl mt-3">
              <span className="text-[11px] font-bold text-[#fbbf24] mt-0.5 shrink-0">⚠</span>
              <div className="flex flex-col gap-0.5 flex-1">
                {importWarnings.map((w, i) => (
                  <span key={i} className="text-[11px] text-[#fcd34d]">
                    {w}
                  </span>
                ))}
              </div>
              <button
                onClick={() => setImportWarnings([])}
                className="text-[10px] text-[#92400e] hover:text-[#fbbf24] cursor-pointer bg-transparent border-none shrink-0 mt-0.5"
              >
                ✕
              </button>
            </div>
          )}

          {/* Scrollable main area with bottom padding for RunBar */}
          <div className="flex-1 overflow-y-auto pb-[96px] pt-3 scrollbar-thin">
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3 px-1 sm:px-2 pb-3">
              <StatCard label="Imported Lineups" value={String(managedLineups.length)} tone="gold" />
              <StatCard label="Players In Pool" value={String(playerRecs.length)} tone="blue" />
              <StatCard label="Locked" value={String(lockedNames.length)} />
              <StatCard label="At Risk" value={String(affectedCount)} tone={affectedCount > 0 ? 'danger' : 'neutral'} />
            </div>

            {/* Results section */}
            {step === 'results' && batchResult && (
              <div className="px-1 sm:px-2 pt-1 pb-2">
                {/* Results summary */}
                <div className="glass-panel rounded-2xl px-4 sm:px-5 py-4 flex items-center gap-2 flex-wrap mb-3">
                  <div>
                    <div className="section-label mb-1">Results</div>
                    <span className="text-lg font-black text-text-primary tracking-tight">Batch Complete</span>
                  </div>
                  <span className="text-[11px] font-bold bg-success-muted text-success border border-success/20 px-3 py-1 rounded-full">
                    ✓{' '}
                    {batchResult.swap_log.reduce((s, l) => s + l.swaps_made, 0)}{' '}
                    swaps applied
                  </span>
                  {batchResult.swap_log.some(l => l.swaps_failed > 0) && (
                    <span className="text-[11px] font-bold bg-danger-muted text-danger border border-danger/20 px-3 py-1 rounded-full">
                      ✕{' '}
                      {batchResult.swap_log.reduce((s, l) => s + l.swaps_failed, 0)}{' '}
                      failed
                    </span>
                  )}
                  <button
                    onClick={() => setStep('command')}
                    className="ml-auto text-[11px] text-text-secondary hover:text-text-primary cursor-pointer bg-transparent border-none"
                  >
                    ← Player Controls
                  </button>
                </div>
                <BatchSwapResultsTable
                  batchResult={batchResult}
                  activeLineupIdx={activeLineupIdx}
                  onSelectLineup={setActiveLineupIdx}
                  salaryCap={SALARY_CAP[site]}
                />
              </div>
            )}

            {/* Player Command Center */}
            <div className="px-1 sm:px-2 py-2">
              <div className="glass-panel rounded-2xl px-5 py-4 mb-3 flex items-center justify-between gap-2 flex-wrap">
                <span className="text-[11px] font-bold text-text-muted uppercase tracking-wide">
                  Player Controls
                  <span className="ml-2 font-normal normal-case text-text-secondary">
                    {playerRecs.length} players · {managedLineups.length} lineups
                  </span>
                </span>
                {step === 'results' && batchResult && (
                  <button
                    onClick={() => {
                      setBatchResult(null)
                      setSwapComparisons(new Map())
                      setManagedLineups(prev =>
                        prev.map(lu => ({ ...lu, players: [...lu.originalPlayers] })),
                      )
                      setStep('command')
                    }}
                    className="text-[11px] text-text-secondary hover:text-[#fda4af] cursor-pointer bg-transparent border-none transition-colors"
                  >
                    ↺ Revert swaps
                  </button>
                )}
              </div>

              <PlayerCommandCenter
                players={playerRecs}
                statuses={statusMap}
                onSetStatus={setPlayerStatus}
                onBulkAction={handleBulkAction}
              />
            </div>
          </div>
        </div>
      )}

      {/* ── RunBar — sticky bottom ───────────────────────────────────────────── */}
      {step !== 'upload' && (
        <RunBar
          lineupCount={managedLineups.length}
          scratchedCount={scratchedNames.length}
          lockedCount={lockedNames.length}
          affectedCount={affectedCount}
          hasSlate={slateFile !== null}
          slateName={slateFile?.name ?? null}
          onSlateSelect={f => setSlateFile(f)}
          loading={batchLoading}
          batchError={batchError}
          hasResults={batchResult !== null}
          exportReady={batchResult !== null}
          contestType={contestType}
          onContestTypeChange={setContestType}
          wProj={wProj}
          wValue={wValue}
          wOwn={wOwn}
          diversityFactor={diversityFactor}
          onWeightChange={(key, val) => {
            if (key === 'proj') setWProj(val)
            else if (key === 'value') setWValue(val)
            else if (key === 'own') setWOwn(val)
            else setDiversityFactor(val)
          }}
          onRun={handleBatchSwap}
          onExport={handleExport}
        />
      )}
    </div>
  )
}
