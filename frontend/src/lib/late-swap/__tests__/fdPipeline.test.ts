/**
 * FanDuel Late Swap Pipeline — Unit Tests
 *
 * Covers: import parsing, export generation, validation, and slot-level
 * ID management across the full FD pipeline.
 *
 * Run with: npx jest --testPathPattern=fdPipeline
 */

import {
  parseCSVRow,
  buildIdToNameMap,
  buildNameToIdMap,
  importFanDuelEntries,
  importFanDuelEntriesSelfContained,
} from '../import/importFanDuelEntries'
import { exportFanDuelLineups } from '../export/exportFanDuelLineups'
import {
  validateFDLineup,
  validateFDLineups,
  hasBlockingValidationErrors,
  blockingErrorSummary,
} from '../validation/validateFDLineup'
import type { FDLineupEntry } from '../models/fdLineup'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const SLATE_CSV = `Id,Position,First Name,Nickname,Last Name,FPPG,Played,Salary,Game,Team,Opponent,Injury Indicator,Injury Details,Roster Position,Starting,Featured
125392-157833,PF/SF,Jalen,Jalen Johnson,Johnson,40.2,24,8500,ATL@BOS 07:30PM ET,ATL,BOS,,,PF/SF,
125392-145304,PG,Trae,Trae Young,Young,42.1,24,9500,ATL@BOS 07:30PM ET,ATL,BOS,,,PG,
125392-171772,C,Brook,Brook Lopez,Lopez,32.5,24,7200,ATL@BOS 07:30PM ET,BOS,ATL,,,C,
125392-184411,SG,Jaylen,Jaylen Brown,Brown,38.9,24,9000,ATL@BOS 07:30PM ET,BOS,ATL,,,SG,
125392-200011,PF,Jayson,Jayson Tatum,Tatum,44.5,24,10500,ATL@BOS 07:30PM ET,BOS,ATL,,,PF,
125392-155501,PG,Dejounte,Dejounte Murray,Murray,35.7,24,7800,ATL@BOS 07:30PM ET,ATL,BOS,,,PG,
125392-166621,SG,Bogdan,Bogdan Bogdanovic,Bogdanovic,28.3,24,5900,ATL@BOS 07:30PM ET,ATL,BOS,,,SG,
125392-177741,SF,Al,Al Horford,Horford,29.1,24,6400,ATL@BOS 07:30PM ET,BOS,ATL,,,SF,
125392-188861,PF,Robert,Robert Williams,Williams,27.8,24,5500,ATL@BOS 07:30PM ET,BOS,ATL,,,PF,
125392-199981,C,Onyeka,Onyeka Okongwu,Okongwu,30.2,24,6000,ATL@BOS 07:30PM ET,ATL,BOS,,,C,`

// Entry CSV with 2 lineups (positional columns, duplicate headers intentional)
const ENTRY_CSV = `Entry ID,Contest ID,Contest Name,Entry Fee,PG,PG,SG,SG,SF,SF,PF,PF,C
10001001,50000001,"NBA Thu Jan 9 ATL@BOS",11.00,125392-145304,125392-155501,125392-184411,125392-166621,125392-177741,125392-157833,125392-200011,125392-188861,125392-171772
10001002,50000001,"NBA Thu Jan 9 ATL@BOS",11.00,125392-145304,125392-155501,125392-184411,125392-166621,125392-177741,125392-157833,125392-200011,125392-199981,125392-171772`

const FD_SELF_CONTAINED_TEMPLATE = `Entry ID,Contest ID,Contest Name,Entry Fee,PG,PG,SG,SG,SF,SF,PF,PF,C,,Player ID + Player Name,Id,Position,First Name,Nickname,Last Name,FPPG,Played,Salary,Game,Team,Opponent
10001001,50000001,"NBA Thu Jan 9 ATL@BOS",11.00,125392-145304,125392-155501,125392-184411,125392-166621,125392-177741,125392-157833,125392-200011,125392-188861,125392-171772,,125392-145304:Trae Young,125392-145304,PG,Trae,Trae Young,Young,42.1,24,9500,ATL@BOS 07:30PM ET,ATL,BOS
10001002,50000001,"NBA Thu Jan 9 ATL@BOS",11.00,125392-145304,125392-155501,125392-184411,125392-166621,125392-177741,125392-157833,125392-200011,125392-199981,125392-171772,,125392-171772:Brook Lopez,125392-171772,C,Brook,Brook Lopez,Lopez,32.5,24,7200,ATL@BOS 07:30PM ET,BOS,ATL
,,,,,,,,,,,,,,125392-155501:Dejounte Murray,125392-155501,PG,Dejounte,Dejounte Murray,Murray,35.7,24,7800,ATL@BOS 07:30PM ET,ATL,BOS
,,,,,,,,,,,,,,125392-184411:Jaylen Brown,125392-184411,SG,Jaylen,Jaylen Brown,Brown,38.9,24,9000,ATL@BOS 07:30PM ET,BOS,ATL
,,,,,,,,,,,,,,125392-166621:Bogdan Bogdanovic,125392-166621,SG,Bogdan,Bogdan Bogdanovic,Bogdanovic,28.3,24,5900,ATL@BOS 07:30PM ET,ATL,BOS
,,,,,,,,,,,,,,125392-177741:Al Horford,125392-177741,SF,Al,Al Horford,Horford,29.1,24,6400,ATL@BOS 07:30PM ET,BOS,ATL
,,,,,,,,,,,,,,125392-157833:Jalen Johnson,125392-157833,SF,Jalen,Jalen Johnson,Johnson,40.2,24,8500,ATL@BOS 07:30PM ET,ATL,BOS
,,,,,,,,,,,,,,125392-200011:Jayson Tatum,125392-200011,PF,Jayson,Jayson Tatum,Tatum,44.5,24,10500,ATL@BOS 07:30PM ET,BOS,ATL
,,,,,,,,,,,,,,125392-188861:Robert Williams,125392-188861,PF,Robert,Robert Williams,Williams,27.8,24,5500,ATL@BOS 07:30PM ET,BOS,ATL
,,,,,,,,,,,,,,125392-199981:Onyeka Okongwu,125392-199981,C,Onyeka,Onyeka Okongwu,Okongwu,30.2,24,6000,ATL@BOS 07:30PM ET,ATL,BOS`

const FD_SELF_CONTAINED_TEMPLATE_NO_PARSEABLE_TIMES = `Entry ID,Contest ID,Contest Name,Entry Fee,PG,PG,SG,SG,SF,SF,PF,PF,C,,Player ID + Player Name,Id,Position,First Name,Nickname,Last Name,FPPG,Played,Salary,Game,Team,Opponent
10001001,50000001,"NBA Main Slate",11.00,125392-145304,125392-155501,125392-184411,125392-166621,125392-177741,125392-157833,125392-200011,125392-188861,125392-171772,,125392-145304:Trae Young,125392-145304,PG,Trae,Trae Young,Young,42.1,24,9500,ATL@BOS tipoff,ATL,BOS
,,,,,,,,,,,,,,125392-155501:Dejounte Murray,125392-155501,PG,Dejounte,Dejounte Murray,Murray,35.7,24,7800,ATL@BOS tipoff,ATL,BOS
,,,,,,,,,,,,,,125392-184411:Jaylen Brown,125392-184411,SG,Jaylen,Jaylen Brown,Brown,38.9,24,9000,ATL@BOS tipoff,BOS,ATL
,,,,,,,,,,,,,,125392-166621:Bogdan Bogdanovic,125392-166621,SG,Bogdan,Bogdan Bogdanovic,Bogdanovic,28.3,24,5900,ATL@BOS tipoff,ATL,BOS
,,,,,,,,,,,,,,125392-177741:Al Horford,125392-177741,SF,Al,Al Horford,Horford,29.1,24,6400,ATL@BOS tipoff,BOS,ATL
,,,,,,,,,,,,,,125392-157833:Jalen Johnson,125392-157833,SF,Jalen,Jalen Johnson,Johnson,40.2,24,8500,ATL@BOS tipoff,ATL,BOS
,,,,,,,,,,,,,,125392-200011:Jayson Tatum,125392-200011,PF,Jayson,Jayson Tatum,Tatum,44.5,24,10500,ATL@BOS tipoff,BOS,ATL
,,,,,,,,,,,,,,125392-188861:Robert Williams,125392-188861,PF,Robert,Robert Williams,Williams,27.8,24,5500,ATL@BOS tipoff,BOS,ATL
,,,,,,,,,,,,,,125392-171772:Brook Lopez,125392-171772,C,Brook,Brook Lopez,Lopez,32.5,24,7200,ATL@BOS tipoff,BOS,ATL`

// ---------------------------------------------------------------------------
// Helper: build minimal FDLineupEntry
// ---------------------------------------------------------------------------

function makeEntry(overrides: Partial<FDLineupEntry> = {}): FDLineupEntry {
  return {
    entryId: '10001001',
    contestId: '50000001',
    contestName: 'NBA Test',
    entryFee: 11,
    slots: [
      { slot: 'PG1', fdPlayerId: '125392-145304', playerName: 'Trae Young',         locked: false },
      { slot: 'PG2', fdPlayerId: '125392-155501', playerName: 'Dejounte Murray',    locked: false },
      { slot: 'SG1', fdPlayerId: '125392-184411', playerName: 'Jaylen Brown',       locked: false },
      { slot: 'SG2', fdPlayerId: '125392-166621', playerName: 'Bogdan Bogdanovic',  locked: false },
      { slot: 'SF1', fdPlayerId: '125392-177741', playerName: 'Al Horford',         locked: false },
      { slot: 'SF2', fdPlayerId: '125392-157833', playerName: 'Jalen Johnson',      locked: false },
      { slot: 'PF1', fdPlayerId: '125392-200011', playerName: 'Jayson Tatum',       locked: false },
      { slot: 'PF2', fdPlayerId: '125392-188861', playerName: 'Robert Williams',    locked: false },
      { slot: 'C',   fdPlayerId: '125392-171772', playerName: 'Brook Lopez',        locked: false },
    ],
    ...overrides,
  }
}

// ===========================================================================
// TEST SUITE 1 — CSV Row Parser
// ===========================================================================

describe('parseCSVRow', () => {
  test('parses a simple comma-separated row', () => {
    expect(parseCSVRow('a,b,c')).toEqual(['a', 'b', 'c'])
  })

  test('handles a quoted field containing a comma', () => {
    expect(parseCSVRow('"NBA Thu, Jan 9 ATL@BOS",11.00,abc')).toEqual([
      'NBA Thu, Jan 9 ATL@BOS', '11.00', 'abc',
    ])
  })

  test('handles escaped double-quotes inside quoted fields', () => {
    expect(parseCSVRow('"He said ""hello""",world')).toEqual(['He said "hello"', 'world'])
  })

  test('returns a single-element array for an empty string', () => {
    expect(parseCSVRow('')).toEqual([''])
  })

  test('preserves trailing empty field', () => {
    expect(parseCSVRow('a,b,')).toEqual(['a', 'b', ''])
  })
})

// ===========================================================================
// TEST SUITE 2 — Slate ID↔Name maps
// ===========================================================================

describe('buildIdToNameMap', () => {
  test('maps composite ID to Nickname', () => {
    const map = buildIdToNameMap(SLATE_CSV)
    expect(map.get('125392-157833')).toBe('Jalen Johnson')
    expect(map.get('125392-145304')).toBe('Trae Young')
    expect(map.get('125392-171772')).toBe('Brook Lopez')
  })

  test('returns empty map for empty text', () => {
    expect(buildIdToNameMap('').size).toBe(0)
  })

  test('returns empty map if Id or Nickname column is missing', () => {
    const badSlate = 'Position,First Name\nPG,Trae'
    expect(buildIdToNameMap(badSlate).size).toBe(0)
  })
})

describe('buildNameToIdMap', () => {
  test('maps player display name to composite ID', () => {
    const map = buildNameToIdMap(SLATE_CSV)
    expect(map.get('Jalen Johnson')).toBe('125392-157833')
    expect(map.get('Brook Lopez')).toBe('125392-171772')
  })

  test('also indexes by lowercase name', () => {
    const map = buildNameToIdMap(SLATE_CSV)
    expect(map.get('jalen johnson')).toBe('125392-157833')
  })
})

// ===========================================================================
// TEST SUITE 3 — FD Entry CSV Import
// ===========================================================================

describe('importFanDuelEntries', () => {
  test('parses correct number of lineups', () => {
    const { lineups } = importFanDuelEntries(ENTRY_CSV, SLATE_CSV)
    expect(lineups).toHaveLength(2)
  })

  test('preserves entry metadata on first lineup', () => {
    const { lineups } = importFanDuelEntries(ENTRY_CSV, SLATE_CSV)
    const first = lineups[0]
    expect(first.entryId).toBe('10001001')
    expect(first.contestId).toBe('50000001')
    expect(first.contestName).toBe('NBA Thu Jan 9 ATL@BOS')
    expect(first.entryFee).toBe(11)
  })

  test('each lineup has exactly 9 slots', () => {
    const { lineups } = importFanDuelEntries(ENTRY_CSV, SLATE_CSV)
    for (const lu of lineups) {
      expect(lu.slots).toHaveLength(9)
    }
  })

  test('resolves player names from slate via ID lookup', () => {
    const { lineups } = importFanDuelEntries(ENTRY_CSV, SLATE_CSV)
    const pg1 = lineups[0].slots.find(s => s.slot === 'PG1')!
    expect(pg1.fdPlayerId).toBe('125392-145304')
    expect(pg1.playerName).toBe('Trae Young')
  })

  test('slot order matches FD CSV column order (PG1, PG2, SG1, SG2, SF1, SF2, PF1, PF2, C)', () => {
    const { lineups } = importFanDuelEntries(ENTRY_CSV, SLATE_CSV)
    const slots = lineups[0].slots.map(s => s.slot)
    expect(slots).toEqual(['PG1', 'PG2', 'SG1', 'SG2', 'SF1', 'SF2', 'PF1', 'PF2', 'C'])
  })

  test('warns about unknown player IDs but still imports the row', () => {
    const badEntry = `Entry ID,Contest ID,Contest Name,Entry Fee,PG,PG,SG,SG,SF,SF,PF,PF,C
10001001,50000001,"NBA Test",11.00,125392-UNKNOWN,125392-155501,125392-184411,125392-166621,125392-177741,125392-157833,125392-200011,125392-188861,125392-171772`
    const { lineups, warnings } = importFanDuelEntries(badEntry, SLATE_CSV)
    expect(lineups).toHaveLength(1)
    expect(warnings.length).toBeGreaterThan(0)
    // Unknown ID falls back to the raw ID as player name
    expect(lineups[0].slots[0].playerName).toBe('125392-UNKNOWN')
  })

  test('returns empty lineups and warnings for an empty entry file', () => {
    const { lineups, warnings } = importFanDuelEntries('', SLATE_CSV)
    expect(lineups).toHaveLength(0)
    expect(warnings.length).toBeGreaterThan(0)
  })
})

describe('importFanDuelEntriesSelfContained', () => {
  test('parses lineups and preserves name-to-id mapping from the entry template itself', () => {
    const { lineups, nameToIdMap, warnings } = importFanDuelEntriesSelfContained(FD_SELF_CONTAINED_TEMPLATE)
    expect(lineups).toHaveLength(2)
    expect(nameToIdMap.get('Trae Young')).toBe('125392-145304')
    expect(nameToIdMap.get('trae young')).toBe('125392-145304')
    expect(warnings).toHaveLength(0)
  })

  test('auto-locks imported FD players when embedded game times are already started', () => {
    const realNow = Date.now
    Date.now = () => new Date('2026-01-10T00:00:00.000Z').valueOf()
    try {
      const { lineups, lockedByGameTime } = importFanDuelEntriesSelfContained(FD_SELF_CONTAINED_TEMPLATE)
      expect(lockedByGameTime).toEqual(expect.arrayContaining(['Trae Young', 'Brook Lopez']))
      expect(lineups[0].slots[0].locked).toBe(true)
      expect(lineups[0].slots[8].locked).toBe(true)
    } finally {
      Date.now = realNow
    }
  })

  test('warns when FD game times are not parseable and auto-lock cannot run', () => {
    const { lineups, lockedByGameTime, warnings } = importFanDuelEntriesSelfContained(FD_SELF_CONTAINED_TEMPLATE_NO_PARSEABLE_TIMES)
    expect(lineups).toHaveLength(1)
    expect(lockedByGameTime).toEqual([])
    expect(warnings).toContain(
      'Could not infer FanDuel game start times from this entry template, so started-player auto-lock did not run. Review Locked Players manually.',
    )
  })
})

// ===========================================================================
// TEST SUITE 4 — FD Upload CSV Export
// ===========================================================================

describe('exportFanDuelLineups', () => {
  test('first line is the correct FD header', () => {
    const csv = exportFanDuelLineups([makeEntry()])
    const firstLine = csv.split('\n')[0]
    expect(firstLine).toBe('Entry ID,Contest ID,Contest Name,Entry Fee,PG,PG,SG,SG,SF,SF,PF,PF,C')
  })

  test('data row contains entry metadata', () => {
    const csv = exportFanDuelLineups([makeEntry()])
    const dataRow = csv.split('\n')[1]
    expect(dataRow).toContain('10001001')
    expect(dataRow).toContain('50000001')
    expect(dataRow).toContain('11.00')
  })

  test('data row contains all 9 composite player IDs', () => {
    const csv = exportFanDuelLineups([makeEntry()])
    const dataRow = csv.split('\n')[1]
    expect(dataRow).toContain('125392-145304')
    expect(dataRow).toContain('125392-171772')
    expect(dataRow).toContain('125392-200011')
  })

  test('contest name with commas is properly quoted', () => {
    const entry = makeEntry({ contestName: 'NBA Thu, Jan 9 ATL@BOS' })
    const csv = exportFanDuelLineups([entry])
    const dataRow = csv.split('\n')[1]
    expect(dataRow).toContain('"NBA Thu, Jan 9 ATL@BOS"')
  })

  test('round-trip: import then export preserves all player IDs', () => {
    const { lineups } = importFanDuelEntries(ENTRY_CSV, SLATE_CSV)
    const csv = exportFanDuelLineups(lineups)
    const rows = csv.split('\n')
    // header + 2 data rows
    expect(rows).toHaveLength(3)

    // First lineup first player ID
    expect(rows[1]).toContain('125392-145304')
    // Second lineup last player (C) differs between the two lineups
    expect(rows[2]).toContain('125392-199981')
  })

  test('generates correct row count for multiple lineups', () => {
    const { lineups } = importFanDuelEntries(ENTRY_CSV, SLATE_CSV)
    const csv = exportFanDuelLineups(lineups)
    // 1 header + n data rows
    expect(csv.split('\n')).toHaveLength(lineups.length + 1)
  })
})

// ===========================================================================
// TEST SUITE 5 — Lineup Validation
// ===========================================================================

describe('validateFDLineup', () => {
  test('passes a valid lineup with no errors or warnings', () => {
    const result = validateFDLineup(makeEntry(), 0)
    expect(result.errors).toHaveLength(0)
    // Expect no blocking warnings about missing entry ID or malformed IDs
    const blockingWarnings = result.warnings.filter(w =>
      w.includes('non-composite') || w.includes('Missing entry')
    )
    expect(blockingWarnings).toHaveLength(0)
  })

  test('flags an empty slot as a blocking error', () => {
    const entry = makeEntry({
      slots: makeEntry().slots.map((s, i) =>
        i === 4 ? { ...s, fdPlayerId: '', playerName: '' } : s
      ),
    })
    const result = validateFDLineup(entry, 0)
    expect(result.errors.length).toBeGreaterThan(0)
    expect(result.errors[0]).toMatch(/empty slot/)
  })

  test('flags duplicate player IDs as a blocking error', () => {
    const slots = [...makeEntry().slots]
    // Make PG1 and PG2 the same player
    slots[1] = { ...slots[1], fdPlayerId: slots[0].fdPlayerId, playerName: slots[0].playerName }
    const entry = makeEntry({ slots })
    const result = validateFDLineup(entry, 0)
    expect(result.errors.some(e => e.includes('Duplicate'))).toBe(true)
  })

  test('warns about non-composite IDs (no dash)', () => {
    const slots = [...makeEntry().slots]
    slots[0] = { ...slots[0], fdPlayerId: '157833', playerName: 'Trae Young' }
    const entry = makeEntry({ slots })
    const result = validateFDLineup(entry, 0)
    expect(result.warnings.some(w => w.includes('non-composite'))).toBe(true)
  })

  test('warns about missing entry ID', () => {
    const entry = makeEntry({ entryId: '' })
    const result = validateFDLineup(entry, 0)
    expect(result.warnings.some(w => w.includes('entry ID'))).toBe(true)
  })
})

describe('validateFDLineups / hasBlockingValidationErrors / blockingErrorSummary', () => {
  test('returns only result entries that have errors or warnings', () => {
    const valid = makeEntry()
    const invalid = makeEntry({
      slots: makeEntry().slots.map((s, i) =>
        i === 0 ? { ...s, fdPlayerId: '' } : s
      ),
    })
    const results = validateFDLineups([valid, invalid])
    // Only the invalid lineup should appear
    expect(results).toHaveLength(1)
    expect(results[0].lineupIdx).toBe(1)
  })

  test('hasBlockingValidationErrors returns false for all-valid lineups', () => {
    const results = validateFDLineups([makeEntry(), makeEntry({ entryId: '99999' })])
    expect(hasBlockingValidationErrors(results)).toBe(false)
  })

  test('hasBlockingValidationErrors returns true when errors present', () => {
    const bad = makeEntry({
      slots: makeEntry().slots.map((s, i) => (i === 0 ? { ...s, fdPlayerId: '' } : s)),
    })
    const results = validateFDLineups([bad])
    expect(hasBlockingValidationErrors(results)).toBe(true)
  })

  test('blockingErrorSummary returns empty string when no blocking errors', () => {
    expect(blockingErrorSummary([])).toBe('')
  })

  test('blockingErrorSummary includes lineup number in message', () => {
    const bad = makeEntry({
      slots: makeEntry().slots.map((s, i) => (i === 3 ? { ...s, fdPlayerId: '' } : s)),
    })
    const results = validateFDLineups([bad])
    const summary = blockingErrorSummary(results)
    expect(summary).toContain('Lineup 1')
  })
})
