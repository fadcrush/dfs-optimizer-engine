/**
 * DraftKings Late Swap — Client-Side Entry Importer
 *
 * Parses the DK combined entry+slate CSV file that DraftKings generates
 * for bulk lineup management. This single file contains:
 *
 *   Row 0:   Header — Entry ID, Contest Name, Contest ID, Entry Fee,
 *                     PG, SG, SF, PF, C, G, F, UTIL, <empty>, Instructions…
 *
 *   Rows 1-N (Entry ID is numeric):
 *            Entry data — entry metadata + player slots as "Name (PlayerID)"
 *
 *   Rows N+1… (Entry ID is empty, data at col 13+):
 *            Slate player pool — Position, Name+ID, Name, ID, RosterPos,
 *                                Salary, Game Info, TeamAbbrev, AvgPPG
 *
 * Parsing client-side (instead of the backend) lets us preserve Entry IDs
 * and player IDs so the export can produce a valid DK bulk-upload CSV.
 */

import type { DKLineupEntry, DKLineupSlot, DKSlot } from '../models/dkLineup'
import { DK_SLOT_ORDER } from '../models/dkLineup'

// ---------------------------------------------------------------------------
// Result type
// ---------------------------------------------------------------------------

export interface DKImportResult {
  lineups: DKLineupEntry[]
  warnings: string[]
  /** playerName.toLowerCase() → numeric DK player ID */
  nameToIdMap: Map<string, string>
  /** numeric DK player ID → display name */
  idToNameMap: Map<string, string>
  /** playerName.toLowerCase() → raw Game Info string (e.g. "HOU@SAS 03/08/2026 08:00PM ET") */
  gameTimesByPlayer: Map<string, string>
  /**
   * Player names (from the imported lineups) whose games have already
   * started based on wall-clock time.  Pre-populated into the Locked
   * Players textarea so users don't have to enter them manually.
   */
  lockedByGameTime: string[]
}

// ---------------------------------------------------------------------------
// CSV row parser (handles quoted fields)
// ---------------------------------------------------------------------------

function parseCSVRow(line: string): string[] {
  const result: string[] = []
  let field = ''
  let inQuotes = false
  for (let i = 0; i < line.length; i++) {
    const c = line[i]
    if (c === '"') {
      if (inQuotes && line[i + 1] === '"') {
        field += '"'
        i++
      } else {
        inQuotes = !inQuotes
      }
    } else if (c === ',' && !inQuotes) {
      result.push(field)
      field = ''
    } else {
      field += c
    }
  }
  result.push(field)
  return result
}

// ---------------------------------------------------------------------------
// Game-time parser
// ---------------------------------------------------------------------------

/**
 * Parse a DK Game Info string (e.g. "HOU@SAS 03/08/2026 08:00PM ET") into a
 * UTC Date. Returns null if the string doesn't match the expected format.
 *
 * Uses Intl.DateTimeFormat with timeZone 'America/New_York' to correctly
 * handle both EST (UTC-5) and EDT (UTC-4) automatically — no hardcoded offset.
 * A 5-minute buffer is applied so a game isn't treated as started until
 * it has definitively tipped off.
 */
function parseETGameTime(gameInfo: string): Date | null {
  const m = gameInfo.match(
    /(\d{2})\/(\d{2})\/(\d{4})\s+(\d{1,2}):(\d{2})(AM|PM)\s+ET/i,
  )
  if (!m) return null
  const [, month, day, year, hourStr, minStr, ampm] = m
  let hour = parseInt(hourStr, 10)
  const min = parseInt(minStr, 10)
  if (ampm.toUpperCase() === 'PM' && hour !== 12) hour += 12
  if (ampm.toUpperCase() === 'AM' && hour === 12) hour = 0

  // DST-aware conversion: probe with EST (UTC-5) then correct using Intl
  const y = parseInt(year, 10)
  const m0 = parseInt(month, 10) - 1
  const d = parseInt(day, 10)
  const probe = new Date(Date.UTC(y, m0, d, hour + 5, min))
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    year: 'numeric', month: 'numeric', day: 'numeric',
    hour: 'numeric', minute: 'numeric', second: 'numeric',
    hour12: false,
  })
  const parts = fmt.formatToParts(probe)
  const get = (type: string) => parseInt(parts.find(p => p.type === type)?.value ?? '0', 10)
  const etH = get('hour') === 24 ? 0 : get('hour')
  const etMs = Date.UTC(get('year'), get('month') - 1, get('day'), etH, get('minute'), get('second'))
  const offsetMinutes = (etMs - probe.getTime()) / 60000
  // UTC = ET local - offset  (+5-min buffer so game must fully start before locking)
  return new Date(Date.UTC(y, m0, d, hour, min) - offsetMinutes * 60000 + 5 * 60 * 1000)
}

// ---------------------------------------------------------------------------
// "Name (PlayerID)" extractor
// ---------------------------------------------------------------------------

function extractNameAndId(cell: string): { name: string; playerId: string } | null {
  const m = cell.match(/^(.+?)\s+\((\d+)\)\s*$/)
  if (!m) return null
  return { name: m[1].trim(), playerId: m[2] }
}

/**
 * Detect whether a CSV is FanDuel format (composite IDs like "127477-84680"
 * in the player slot columns) vs DraftKings format ("Name (PlayerID)").
 *
 * Looks at the first entry row's player slot columns (4–11).
 */
export function detectCSVSite(csvText: string): 'DK' | 'FD' | null {
  const lines = csvText.split('\n')
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim()
    if (!line) continue
    const cols = parseCSVRow(line)
    const entryId = cols[0]?.trim() ?? ''
    if (!/^\d+$/.test(entryId)) continue // skip non-entry rows

    // Check the first non-empty player slot (cols 4-11)
    for (let c = 4; c <= 11; c++) {
      const cell = cols[c]?.trim() ?? ''
      if (!cell) continue
      // FD composite ID: "127477-84680"
      if (/^\d+-\d+$/.test(cell)) return 'FD'
      // DK format: "Player Name (12345)"
      if (/\(\d+\)\s*$/.test(cell)) return 'DK'
      break
    }
    break
  }
  return null
}

// ---------------------------------------------------------------------------
// Main importer
// ---------------------------------------------------------------------------

/**
 * Parse a DraftKings combined entry+slate CSV.
 *
 * @param csvText  Full text content of the DK entries file.
 * @returns        Parsed lineups, player-ID maps, and auto-locked players.
 */
export function importDraftKingsEntries(csvText: string): DKImportResult {
  const warnings: string[] = []
  const lineups: DKLineupEntry[] = []
  const nameToIdMap = new Map<string, string>()
  const idToNameMap = new Map<string, string>()
  const gameTimesByPlayer = new Map<string, string>()
  const autoLockedNames = new Set<string>()
  const now = new Date()

  // DK entry CSV column indices for player slots
  // Header: Entry ID(0), Contest Name(1), Contest ID(2), Entry Fee(3),
  //         PG(4), SG(5), SF(6), PF(7), C(8), G(9), F(10), UTIL(11)
  const ENTRY_SLOT_COLS = [4, 5, 6, 7, 8, 9, 10, 11]

  // Player pool data starts at col 13 in non-entry rows
  const COL_NAME_ID = 14  // "Name + ID"  e.g. "Victor Wembanyama (42210956)"
  const COL_NAME    = 15  // "Name"
  const COL_ID      = 16  // "ID"
  const COL_GAME    = 19  // "Game Info" e.g. "HOU@SAS 03/08/2026 08:00PM ET"

  const lines = csvText.split('\n')

  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim()
    if (!line) continue

    const cols = parseCSVRow(line)
    const entryId = cols[0]?.trim() ?? ''

    if (/^\d+$/.test(entryId)) {
      // ── Entry row ──────────────────────────────────────────────────────────
      const contestName = cols[1]?.trim() ?? ''
      const contestId   = cols[2]?.trim() ?? ''
      const entryFee    = cols[3]?.trim() ?? ''

      const slots: DKLineupSlot[] = []
      for (let s = 0; s < DK_SLOT_ORDER.length; s++) {
        const colIdx = ENTRY_SLOT_COLS[s]
        const cell   = cols[colIdx]?.trim() ?? ''
        if (!cell) continue

        const parsed = extractNameAndId(cell)
        if (parsed) {
          slots.push({ slot: DK_SLOT_ORDER[s], playerName: parsed.name, playerId: parsed.playerId })
          nameToIdMap.set(parsed.name.toLowerCase(), parsed.playerId)
          idToNameMap.set(parsed.playerId, parsed.name)
        } else {
          slots.push({ slot: DK_SLOT_ORDER[s], playerName: cell, playerId: '' })
          if (cell) warnings.push(`Could not extract player ID from slot "${DK_SLOT_ORDER[s]}": "${cell}"`)
        }
      }

      if (slots.length > 0) {
        lineups.push({ entryId, contestName, contestId, entryFee, slots })
      }

      // DK embeds the first N player-pool rows inside the entry rows (at cols 13+).
      // Process them here so we don't miss players like Wembanyama whose pool
      // entry only appears in an entry row, not in a standalone empty-entryId row.
      if (cols.length > COL_GAME) {
        const nameId  = cols[COL_NAME_ID]?.trim() ?? ''
        const name    = cols[COL_NAME]?.trim() ?? ''
        const id      = cols[COL_ID]?.trim() ?? ''
        const game    = cols[COL_GAME]?.trim() ?? ''
        // Skip if col 14 is a column header (text like "Name + ID")
        if (nameId && !nameId.toLowerCase().startsWith('name')) {
          const resolvedName = name || extractNameAndId(nameId)?.name
          const resolvedId   = id   || extractNameAndId(nameId)?.playerId
          if (resolvedName && resolvedId) {
            nameToIdMap.set(resolvedName.toLowerCase(), resolvedId)
            idToNameMap.set(resolvedId, resolvedName)
          }
          if (resolvedName && game && !game.toLowerCase().startsWith('game')) {
            gameTimesByPlayer.set(resolvedName.toLowerCase(), game)
            const gameTime = parseETGameTime(game)
            if (gameTime && gameTime <= now) {
              autoLockedNames.add(resolvedName)
            }
          }
        }
      }
    } else if (!entryId && cols.length > COL_GAME) {
      // ── Player-pool row ────────────────────────────────────────────────────
      const nameId  = cols[COL_NAME_ID]?.trim() ?? ''
      const name    = cols[COL_NAME]?.trim() ?? ''
      const id      = cols[COL_ID]?.trim() ?? ''
      const game    = cols[COL_GAME]?.trim() ?? ''

      // Prefer explicit Name + ID columns; fall back to parsing the combined col
      const resolvedName = name || extractNameAndId(nameId)?.name
      const resolvedId   = id   || extractNameAndId(nameId)?.playerId

      if (resolvedName && resolvedId) {
        nameToIdMap.set(resolvedName.toLowerCase(), resolvedId)
        idToNameMap.set(resolvedId, resolvedName)
      }

      if (resolvedName && game) {
        gameTimesByPlayer.set(resolvedName.toLowerCase(), game)
        const gameTime = parseETGameTime(game)
        if (gameTime && gameTime <= now) {
          autoLockedNames.add(resolvedName)
        }
      }
    }
  }

  // Only report auto-locked players that actually appear in one of the lineups
  const allLineupPlayers = new Set<string>()
  for (const lu of lineups) {
    for (const slot of lu.slots) allLineupPlayers.add(slot.playerName.toLowerCase())
  }
  const lockedByGameTime = [...autoLockedNames].filter(n =>
    allLineupPlayers.has(n.toLowerCase()),
  )

  if (lineups.length === 0) {
    warnings.push(
      'No lineup entries found — make sure the file contains rows with numeric Entry IDs (DraftKings entry export format).',
    )
  }

  return { lineups, warnings, nameToIdMap, idToNameMap, gameTimesByPlayer, lockedByGameTime }
}

// ---------------------------------------------------------------------------
// Re-export the generic file reader (used by the page alongside FD importer)
// ---------------------------------------------------------------------------

export function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = e => resolve(e.target?.result as string)
    reader.onerror = () => reject(new Error('Failed to read file'))
    reader.readAsText(file)
  })
}
