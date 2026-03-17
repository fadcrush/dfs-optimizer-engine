/**
 * FanDuel Late Swap — Entry CSV Exporter
 *
 * Generates a FanDuel bulk-upload CSV from a list of FDLineupEntry objects.
 * The output is directly uploadable to FanDuel's "Edit Lineup" / bulk-upload tool.
 *
 * Output format:
 *   Entry ID,Contest ID,Contest Name,Entry Fee,PG,PG,SG,SG,SF,SF,PF,PF,C
 *   12345678,98765432,"NBA Thu Jan 9th ...",11.00,125392-157833,125392-145304,...
 *
 * Player ID columns contain the full composite IDs ("<slateId>-<playerId>").
 */

import type { FDLineupEntry } from '../models/fdLineup'

// ---------------------------------------------------------------------------
// CSV helpers
// ---------------------------------------------------------------------------

/**
 * Wrap a field value in double-quotes if it contains commas, quotes, or newlines.
 * Always returns a plain string otherwise.
 */
function csvQuote(value: string): string {
  if (value.includes(',') || value.includes('"') || value.includes('\n')) {
    return `"${value.replace(/"/g, '""')}"`
  }
  return value
}

// ---------------------------------------------------------------------------
// Export function
// ---------------------------------------------------------------------------

/**
 * Serialise an array of FDLineupEntry objects into a FanDuel upload CSV string.
 *
 * @param lineups  The post-swap lineup entries to export.
 * @returns        A CSV string ready to be saved as a .csv file.
 */
export function exportFanDuelLineups(lineups: FDLineupEntry[]): string {
  const header = 'Entry ID,Contest ID,Contest Name,Entry Fee,PG,PG,SG,SG,SF,SF,PF,PF,C'

  const rows = lineups.map(lu => {
    const meta = [
      csvQuote(lu.entryId),
      csvQuote(lu.contestId),
      csvQuote(lu.contestName),
      lu.entryFee.toFixed(2),
    ]
    // 9 player composite IDs in slot order
    const players = lu.slots.map(s => csvQuote(s.fdPlayerId))
    return [...meta, ...players].join(',')
  })

  return [header, ...rows].join('\n')
}

// ---------------------------------------------------------------------------
// Browser download helper
// ---------------------------------------------------------------------------

/**
 * Trigger a browser file download with CSV content.
 *
 * @param content   The CSV text to download.
 * @param filename  Suggested filename for the saved file.
 */
export function downloadCSV(content: string, filename: string): void {
  const blob = new Blob([content], { type: 'text/csv;charset=utf-8;' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
