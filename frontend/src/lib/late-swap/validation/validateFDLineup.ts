/**
 * FanDuel Late Swap — Lineup Validator
 *
 * Validates FDLineupEntry objects before export to catch issues that would
 * cause a FanDuel upload to fail or produce incorrect results.
 */

import type { FDLineupEntry } from '../models/fdLineup'

// ---------------------------------------------------------------------------
// Result types
// ---------------------------------------------------------------------------

export interface FDValidationResult {
  lineupIdx: number
  entryId: string
  /** Hard errors — block export. */
  errors: string[]
  /** Soft warnings — export can proceed but user should review. */
  warnings: string[]
}

// ---------------------------------------------------------------------------
// Single lineup validator
// ---------------------------------------------------------------------------

/**
 * Validate a single FDLineupEntry.
 * Returns a result object with errors and warnings (both may be empty).
 */
export function validateFDLineup(lineup: FDLineupEntry, idx: number): FDValidationResult {
  const errors:   string[] = []
  const warnings: string[] = []

  // --- Check 9 slots are all filled -----------------------------------------
  const emptySlots = lineup.slots.filter(s => !s.fdPlayerId.trim())
  if (emptySlots.length > 0) {
    errors.push(
      `${emptySlots.length} empty slot(s): ${emptySlots.map(s => s.slot).join(', ')}`
    )
  }

  // --- Check for duplicate player IDs ---------------------------------------
  const ids = lineup.slots.map(s => s.fdPlayerId.trim()).filter(Boolean)
  const idCounts = new Map<string, number>()
  for (const id of ids) idCounts.set(id, (idCounts.get(id) ?? 0) + 1)
  const dupeIds = [...idCounts.entries()].filter(([, n]) => n > 1).map(([id]) => id)
  if (dupeIds.length > 0) {
    errors.push(`Duplicate player ID(s): ${dupeIds.join(', ')}`)
  }

  // --- Check composite ID format "<slateId>-<playerId>" ---------------------
  const malformed = lineup.slots.filter(
    s => s.fdPlayerId.trim() && !s.fdPlayerId.includes('-')
  )
  if (malformed.length > 0) {
    warnings.push(
      `${malformed.length} slot(s) have non-composite IDs (upload may fail): ` +
      malformed.map(s => `${s.slot}="${s.fdPlayerId}"`).join(', ')
    )
  }

  // --- Check missing entry metadata ----------------------------------------
  if (!lineup.entryId) {
    warnings.push('Missing entry ID — lineup may not match an existing FD contest entry')
  }

  return { lineupIdx: idx, entryId: lineup.entryId, errors, warnings }
}

// ---------------------------------------------------------------------------
// Batch validator
// ---------------------------------------------------------------------------

/**
 * Validate an array of FDLineupEntry objects.
 * Only returns results that have at least one error or warning.
 */
export function validateFDLineups(lineups: FDLineupEntry[]): FDValidationResult[] {
  return lineups
    .map((lu, i) => validateFDLineup(lu, i))
    .filter(r => r.errors.length > 0 || r.warnings.length > 0)
}

/**
 * Returns true if any of the provided validation results contain blocking errors.
 */
export function hasBlockingValidationErrors(results: FDValidationResult[]): boolean {
  return results.some(r => r.errors.length > 0)
}

/**
 * Generate a compact human-readable summary of blocking errors for display.
 */
export function blockingErrorSummary(results: FDValidationResult[]): string {
  const blocking = results.filter(r => r.errors.length > 0)
  if (blocking.length === 0) return ''
  return blocking
    .map(r => `Lineup ${r.lineupIdx + 1}: ${r.errors.join('; ')}`)
    .join(' | ')
}
