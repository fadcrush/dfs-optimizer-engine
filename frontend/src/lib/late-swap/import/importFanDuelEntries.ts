/**
 * FanDuel Late Swap — Entry CSV Importer
 *
 * Browser-side parser for FanDuel entry and slate CSV files.
 * Does NOT call the backend — runs entirely in the browser so that player IDs
 * and entry metadata are preserved through the full late-swap pipeline.
 *
 * FD Entry CSV format:
 *   Row 0  — header: Entry ID,Contest ID,Contest Name,Entry Fee,PG,PG,SG,SG,SF,SF,PF,PF,C,...
 *   Rows 1+ — data:  Columns are positional (duplicate header names are NOT unique)
 *     col 0 — Entry ID
 *     col 1 — Contest ID
 *     col 2 — Contest Name
 *     col 3 — Entry Fee
 *     cols 4-12 — 9 player composite IDs ("125392-157833")
 *
 * FD Slate CSV format:
 *   Header contains at minimum: Id, Nickname
 *     Id       — full composite ID "<slateId>-<playerId>" e.g. "125392-157833"
 *     Nickname — player display name e.g. "Jalen Johnson"
 */

import type { FDLineupEntry, FDLineupSlot, FDSlot } from '../models/fdLineup'
import { FD_SLOT_ORDER } from '../models/fdLineup'

// ---------------------------------------------------------------------------
// Public result types
// ---------------------------------------------------------------------------

export interface ImportFDResult {
  lineups: FDLineupEntry[]
  /** Non-fatal issues found during parsing — should surface to the user. */
  warnings: string[]
}

// ---------------------------------------------------------------------------
// CSV row parser
// ---------------------------------------------------------------------------

/**
 * Parse a single CSV line into string fields, correctly handling quoted fields
 * that may contain commas or newlines.
 */
export function parseCSVRow(line: string): string[] {
  const result: string[] = []
  let current = ''
  let inQuote = false

  for (let i = 0; i < line.length; i++) {
    const ch = line[i]
    if (ch === '"') {
      if (inQuote && line[i + 1] === '"') {
        // Escaped quote inside quoted field
        current += '"'
        i++
      } else {
        inQuote = !inQuote
      }
    } else if (ch === ',' && !inQuote) {
      result.push(current)
      current = ''
    } else {
      current += ch
    }
  }

  result.push(current)
  return result
}

// ---------------------------------------------------------------------------
// Slate parsing helpers
// ---------------------------------------------------------------------------

/**
 * Parse a FD slate CSV and return a Map from composite player ID to display name.
 *   e.g. "125392-157833" → "Jalen Johnson"
 */
export function buildIdToNameMap(slateCsvText: string): Map<string, string> {
  const map = new Map<string, string>()
  const lines = slateCsvText.split(/\r?\n/)
  if (lines.length < 2) return map

  const headers = parseCSVRow(lines[0])
  const idIdx = headers.findIndex(h => h.trim().toLowerCase() === 'id')
  const nicknameIdx = headers.findIndex(h => h.trim().toLowerCase() === 'nickname')

  if (idIdx === -1 || nicknameIdx === -1) return map

  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim()
    if (!line) continue
    const row = parseCSVRow(line)
    const id = row[idIdx]?.trim()
    const name = row[nicknameIdx]?.trim()
    if (id && name) map.set(id, name)
  }

  return map
}

/**
 * Parse a FD slate CSV and return a Map from display name to composite player ID.
 * Used for reverse lookup when only player names are available (e.g. batch swap results).
 *   e.g. "Jalen Johnson" → "125392-157833"
 *
 * Note: if a name maps to multiple IDs (rare), the last occurrence wins.
 */
export function buildNameToIdMap(slateCsvText: string): Map<string, string> {
  const idToName = buildIdToNameMap(slateCsvText)
  const map = new Map<string, string>()
  for (const [id, name] of idToName) {
    map.set(name, id)
    // Also index by lowercase for case-insensitive lookup
    map.set(name.toLowerCase(), id)
  }
  return map
}

// ---------------------------------------------------------------------------
// Entry CSV parser
// ---------------------------------------------------------------------------

/**
 * Parse a FanDuel entry CSV and cross-reference player IDs with the slate CSV
 * to derive player display names.
 *
 * @param entryCsvText  Contents of the FD entry CSV file (e.g. DKEntries.csv analogue)
 * @param slateCsvText  Contents of the FD players-list (slate) CSV file
 * @returns             Parsed lineup entries with full metadata and slot-bound player IDs
 */
export function importFanDuelEntries(
  entryCsvText: string,
  slateCsvText: string,
): ImportFDResult {
  const idToName = buildIdToNameMap(slateCsvText)
  const warnings: string[] = []
  const lineups: FDLineupEntry[] = []

  const lines = entryCsvText.split(/\r?\n/)
  if (lines.length < 2) {
    return { lineups: [], warnings: ['Entry file appears empty or has no data rows'] }
  }

  // Row 0 is the header — skip it.  We parse positionally to avoid duplicate-header issues.
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim()
    if (!line) continue

    const row = parseCSVRow(line)

    // Minimum: 4 metadata + 9 player columns = 13
    if (row.length < 13) {
      warnings.push(`Row ${i + 1}: only ${row.length} columns (expected ≥13) — skipped`)
      continue
    }

    const entryId    = row[0]?.trim() ?? ''
    const contestId  = row[1]?.trim() ?? ''
    const contestName = row[2]?.trim() ?? ''
    const entryFee   = parseFloat(row[3]?.replace(/[^0-9.]/g, '') ?? '0') || 0

    // Columns 4–12 are the 9 player composite IDs
    const compositeIds = row.slice(4, 13).map(c => c.trim())

    const slots: FDLineupSlot[] = (FD_SLOT_ORDER as FDSlot[]).map((slot, idx) => {
      const fdPlayerId = compositeIds[idx] ?? ''
      const playerName = fdPlayerId
        ? (idToName.get(fdPlayerId) ?? fdPlayerId)   // fallback to raw ID if slate lookup fails
        : ''
      if (fdPlayerId && !idToName.has(fdPlayerId)) {
        warnings.push(`Row ${i + 1} slot ${idx + 1}: ID "${fdPlayerId}" not found in slate — using raw ID as name`)
      }
      return { slot, fdPlayerId, playerName, locked: false }
    })

    lineups.push({ entryId, contestId, contestName, entryFee, slots })
  }

  return { lineups, warnings }
}

// ---------------------------------------------------------------------------
// Self-contained FD importer (no separate players-list required)
// ---------------------------------------------------------------------------

/**
 * FD entry upload template column indices for the embedded player-pool section.
 *
 * The FD upload template is a combined entry+slate CSV, exactly like DK:
 *   cols  0-3   — Entry metadata  (entry_id, contest_id, contest_name, entry_fee)
 *   cols  4-12  — 9 player composite IDs  ("127310-157808")
 *   col  13     — separator (empty or misc)
 *   col  14     — "Player ID + Player Name"  e.g. "127310-157808:Cade Cunningham"
 *   col  15     — "Id"                       e.g. "127310-157808"
 *   col  16     — Position
 *   col  17     — First Name
 *   col  18     — Nickname (display name)    e.g. "Cade Cunningham"
 *
 * Pool data appears both embedded in entry rows (cols 13+) AND in standalone
 * empty-entry rows at the bottom of the file — same structure as DK.
 */
const FD_POOL_NAME_ID_COL = 14   // "127310-157808:Cade Cunningham" or header text
const FD_POOL_ID_COL      = 15   // full composite ID "127310-157808"
const FD_POOL_NAME_COL    = 18   // Nickname / display name

export interface ImportFDSelfContainedResult {
  lineups: FDLineupEntry[]
  warnings: string[]
  /** display name → composite player ID (e.g. "Cade Cunningham" → "127310-157808") */
  nameToIdMap: Map<string, string>
  /** composite player ID → display name */
  idToNameMap: Map<string, string>
  /** Imported lineup players whose games have already started. */
  lockedByGameTime: string[]
}

function parseContestDate(contestName: string): Date | null {
  const slashMatch = contestName.match(/(\d{1,2})\/(\d{1,2})\/(\d{4})/)
  if (slashMatch) {
    const [, month, day, year] = slashMatch
    return new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), 12, 0, 0))
  }

  const monthMatch = contestName.match(/\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b\s+(\d{1,2})(?:\b|,)?/i)
  if (!monthMatch) return null

  const monthNames = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
  const monthIndex = monthNames.indexOf(monthMatch[1].slice(0, 3).toLowerCase())
  if (monthIndex < 0) return null

  const day = Number(monthMatch[2])
  const now = new Date(Date.now())
  let year = now.getUTCFullYear()

  // Handle season files imported around New Year.
  if (monthIndex >= 10 && now.getUTCMonth() <= 1) year -= 1
  if (monthIndex <= 1 && now.getUTCMonth() >= 10) year += 1

  return new Date(Date.UTC(year, monthIndex, day, 12, 0, 0))
}

/**
 * Convert a local Eastern Time (America/New_York) hour+minute on a given date
 * to a UTC Date, respecting DST automatically via Intl.DateTimeFormat.
 *
 * Adds a 5-minute buffer so a game is not treated as locked until it has
 * definitively started.
 */
function etLocalToUTC(
  year: number,
  month0: number,
  day: number,
  hour24: number,
  min: number,
): Date {
  // Step 1: approximate UTC by assuming EST (UTC-5) as starting point.
  // This gets us within 1 hour of the real UTC time, which is good enough
  // to correctly identify whether DST is active on that date.
  const probe = new Date(Date.UTC(year, month0, day, hour24 + 5, min))

  // Step 2: ask Intl what Eastern time that UTC probe shows.
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    year: 'numeric', month: 'numeric', day: 'numeric',
    hour: 'numeric', minute: 'numeric', second: 'numeric',
    hour12: false,
  })
  const parts = fmt.formatToParts(probe)
  const get = (type: string) => parseInt(parts.find(p => p.type === type)?.value ?? '0', 10)
  const etH = get('hour') === 24 ? 0 : get('hour')
  // Step 3: The difference (ET displayed - ET input) reveals the correction needed.
  // offsetMinutes = (etMs - probe.getTime()) / 60000 (negative for UTC-4 / UTC-5)
  const etMs = Date.UTC(get('year'), get('month') - 1, get('day'), etH, get('minute'), get('second'))
  const offsetMinutes = (etMs - probe.getTime()) / 60000

  // Step 4: UTC = Date.UTC(ET time) - offsetMinutes  (subtracting a negative = adding)
  return new Date(Date.UTC(year, month0, day, hour24, min) - offsetMinutes * 60000 + 5 * 60 * 1000)
}

function parseFDGameTime(gameText: string, fallbackDate: Date | null): Date | null {
  const withDate = gameText.match(/(\d{1,2})\/(\d{1,2})\/(\d{4})\s+(\d{1,2}):(\d{2})(AM|PM)\s+ET/i)
  if (withDate) {
    const [, month, day, year, hourText, minuteText, ampm] = withDate
    let hour = Number(hourText)
    if (ampm.toUpperCase() === 'PM' && hour !== 12) hour += 12
    if (ampm.toUpperCase() === 'AM' && hour === 12) hour = 0
    return etLocalToUTC(Number(year), Number(month) - 1, Number(day), hour, Number(minuteText))
  }

  const timeOnly = gameText.match(/(\d{1,2}):(\d{2})(AM|PM)\s+ET/i)
  if (!timeOnly || !fallbackDate) return null

  const [, hourText, minuteText, ampm] = timeOnly
  let hour = Number(hourText)
  if (ampm.toUpperCase() === 'PM' && hour !== 12) hour += 12
  if (ampm.toUpperCase() === 'AM' && hour === 12) hour = 0

  return etLocalToUTC(
    fallbackDate.getUTCFullYear(),
    fallbackDate.getUTCMonth(),
    fallbackDate.getUTCDate(),
    hour,
    Number(minuteText),
  )
}

/**
 * Parse a FanDuel entry upload template that contains its own player-pool data.
 *
 * This is the recommended import path for late swap. It does NOT require a
 * separate FD players-list (slate) CSV — all required name/ID data is embedded
 * in the template itself, in the player-pool columns (14-18) present in every row.
 *
 * Fixes the slate-ID mismatch bug: the loaded server slate may be from a
 * different contest (e.g. "125392-xxxxx") than the uploaded entry template
 * (e.g. "127310-xxxxx"). Using the template's own pool data always gives the
 * correct ID→name mapping.
 */
export function importFanDuelEntriesSelfContained(csvText: string): ImportFDSelfContainedResult {
  const lines = csvText.split(/\r?\n/)
  const idToName = new Map<string, string>()
  const nameToId = new Map<string, string>()
  const gameById = new Map<string, string>()
  const warnings: string[] = []
  const autoLockedIds = new Set<string>()
  const parseableGameTimeIds = new Set<string>()
  const now = new Date(Date.now())

  const header = lines.length > 0 ? parseCSVRow(lines[0]) : []
  const findColumnIndex = (aliases: string[], fallback: number, startIndex = 0) => {
    const idx = header.findIndex((col, index) => index >= startIndex && aliases.includes(col.trim().toLowerCase()))
    return idx >= 0 ? idx : fallback
  }

  const poolIdCol = findColumnIndex(['id'], FD_POOL_ID_COL, 13)
  const poolNameCol = findColumnIndex(['nickname'], FD_POOL_NAME_COL, 13)
  const poolGameCol = findColumnIndex(['game', 'game info'], -1, 13)
  const contestNameCol = findColumnIndex(['contest name'], 2)

  let slateDate: Date | null = null
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim()
    if (!line) continue
    const row = parseCSVRow(line)
    const entryId = row[0]?.trim() ?? ''
    if (!/^\d+$/.test(entryId)) continue
    const contestName = row[contestNameCol]?.trim() ?? ''
    const parsedDate = parseContestDate(contestName)
    if (parsedDate) {
      slateDate = parsedDate
      break
    }
  }

  // ── Pass 1: build ID↔name maps from ALL rows that carry pool data ─────────
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim()
    if (!line) continue
    const row = parseCSVRow(line)
    if (row.length <= FD_POOL_NAME_COL) continue

    const nameIdText  = row[FD_POOL_NAME_ID_COL]?.trim() ?? ''
    // Skip the embedded pool-column-header row ("Player ID + Player Name")
    if (nameIdText.toLowerCase().startsWith('player id')) continue

    const compositeId = row[poolIdCol]?.trim() ?? ''
    const nickname    = row[poolNameCol]?.trim() ?? ''
    const gameText    = poolGameCol >= 0 ? (row[poolGameCol]?.trim() ?? '') : ''

    // Skip column-header alias rows ("Id", "Nickname") and empty fields
    if (!compositeId || compositeId.toLowerCase() === 'id') continue
    if (!nickname || nickname.toLowerCase() === 'nickname') continue
    if (!compositeId.includes('-')) continue   // must be "slateId-playerId" format

    idToName.set(compositeId, nickname)
    nameToId.set(nickname, compositeId)
    nameToId.set(nickname.toLowerCase(), compositeId)  // case-insensitive lookup
    if (gameText && !gameText.toLowerCase().startsWith('game')) {
      gameById.set(compositeId, gameText)
      const gameTime = parseFDGameTime(gameText, slateDate)
      if (gameTime) {
        parseableGameTimeIds.add(compositeId)
        if (gameTime <= now) {
          autoLockedIds.add(compositeId)
        }
      }
    }
  }

  // ── Pass 2: parse entry rows ───────────────────────────────────────────────
  const lineups: FDLineupEntry[] = []

  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim()
    if (!line) continue
    const row = parseCSVRow(line)

    const entryId = row[0]?.trim() ?? ''
    if (!/^\d+$/.test(entryId)) continue   // entry rows have a numeric ID

    if (row.length < 13) {
      warnings.push(`Row ${i + 1}: only ${row.length} columns (expected ≥13) — skipped`)
      continue
    }

    const contestId   = row[1]?.trim() ?? ''
    const contestName = row[2]?.trim() ?? ''
    const entryFee    = parseFloat(row[3]?.replace(/[^0-9.]/g, '') ?? '0') || 0

    // Columns 4–12 are the 9 player composite IDs
    const compositeIds = row.slice(4, 13).map(c => c.trim())

    const slots: FDLineupSlot[] = (FD_SLOT_ORDER as FDSlot[]).map((slot, idx) => {
      const fdPlayerId = compositeIds[idx] ?? ''
      const playerName = fdPlayerId
        ? (idToName.get(fdPlayerId) ?? fdPlayerId)  // fallback to raw ID if unresolved
        : ''
      if (fdPlayerId && !idToName.has(fdPlayerId)) {
        warnings.push(`Entry ${entryId} slot ${idx + 1}: player ID "${fdPlayerId}" not found in pool data — using raw ID as name`)
      }
      return { slot, fdPlayerId, playerName, locked: autoLockedIds.has(fdPlayerId) }
    })

    lineups.push({ entryId, contestId, contestName, entryFee, slots })
  }

  if (lineups.length === 0) {
    warnings.push(
      'No lineup entries found — make sure the file is the FanDuel entry upload template (rows with numeric Entry IDs).',
    )
  }

  const allLineupPlayers = new Set<string>()
  const importedLineupIds = new Set<string>()
  for (const lineup of lineups) {
    for (const slot of lineup.slots) {
      allLineupPlayers.add(slot.playerName.toLowerCase())
      if (slot.fdPlayerId) importedLineupIds.add(slot.fdPlayerId)
    }
  }

  const importedWithParseableTimes = [...importedLineupIds].filter(id => parseableGameTimeIds.has(id)).length
  const importedWithoutParseableTimes = importedLineupIds.size - importedWithParseableTimes

  if (lineups.length > 0 && importedLineupIds.size > 0) {
    if (importedWithParseableTimes === 0) {
      warnings.push(
        'Could not infer FanDuel game start times from this entry template, so started-player auto-lock did not run. Review Locked Players manually.',
      )
    } else if (importedWithoutParseableTimes > 0) {
      warnings.push(
        `Could only infer FanDuel game start times for ${importedWithParseableTimes} of ${importedLineupIds.size} imported players. Review Locked Players manually for the rest.`,
      )
    }
  }

  const lockedByGameTime = [...autoLockedIds]
    .map(id => idToName.get(id) ?? '')
    .filter(name => name && allLineupPlayers.has(name.toLowerCase()))

  return { lineups, warnings, nameToIdMap: nameToId, idToNameMap: idToName, lockedByGameTime }
}

// ---------------------------------------------------------------------------
// File read helper
// ---------------------------------------------------------------------------

/**
 * Read a browser File object as a UTF-8 text string.
 */
export function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload  = e => resolve((e.target?.result as string) ?? '')
    reader.onerror = () => reject(new Error(`Failed to read file: ${file.name}`))
    reader.readAsText(file, 'utf-8')
  })
}
