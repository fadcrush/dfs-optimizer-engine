/**
 * DraftKings Late Swap — Client-Side Lineup Exporter
 *
 * Generates a DraftKings bulk-upload CSV from a set of DKLineupEntry objects.
 *
 * Output format:
 *   Entry ID,Contest Name,Contest ID,Entry Fee,PG,SG,SF,PF,C,G,F,UTIL
 *   5084162019,NBA $12K...,188663854,$1,Name (PlayerID),Name (PlayerID),…
 *
 * This is the same format as the original DK entry export, with player slots
 * in "Name (PlayerID)" format as required by the DK bulk-upload tool.
 */

import type { DKLineupEntry } from '../models/dkLineup'

// ---------------------------------------------------------------------------
// CSV field escaper
// ---------------------------------------------------------------------------

function csvField(value: string): string {
  if (value.includes(',') || value.includes('"') || value.includes('\n')) {
    return `"${value.replace(/"/g, '""')}"`
  }
  return value
}

// ---------------------------------------------------------------------------
// Exporter
// ---------------------------------------------------------------------------

/**
 * Generate a DraftKings bulk-upload CSV string from lineup entry data.
 *
 * Player slots are written in "Name (PlayerID)" format.
 * Entries with missing player IDs fall back to bare player names.
 *
 * @param lineups  Array of DKLineupEntry objects (post-swap).
 * @returns        CSV string ready for DK bulk-upload.
 */
export function exportDraftKingsLineups(lineups: DKLineupEntry[]): string {
  const header = 'Entry ID,Contest Name,Contest ID,Entry Fee,PG,SG,SF,PF,C,G,F,UTIL'

  const rows = lineups.map(lineup => {
    const slotValues = lineup.slots.map(slot =>
      slot.playerId
        ? `${slot.playerName} (${slot.playerId})`
        : slot.playerName,
    )

    return [
      csvField(lineup.entryId),
      csvField(lineup.contestName),
      csvField(lineup.contestId),
      csvField(lineup.entryFee),
      ...slotValues,
    ].join(',')
  })

  return [header, ...rows].join('\n') + '\n'
}

// ---------------------------------------------------------------------------
// Download helper
// ---------------------------------------------------------------------------

/**
 * Trigger a browser download for the given CSV content.
 * Shared by both DK and FD export paths.
 */
export function downloadCSV(content: string, filename: string): void {
  const blob = new Blob([content], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
