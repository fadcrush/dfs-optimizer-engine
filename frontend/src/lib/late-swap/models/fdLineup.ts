/**
 * FanDuel Late Swap — Lineup Model
 *
 * Represents a FanDuel entry-file lineup with full entry metadata and
 * slot-bound player IDs. This is the authoritative model for FD lineups
 * throughout the late-swap pipeline.
 */

/** Internal slot keys for a FanDuel NBA 9-player lineup. */
export type FDSlot = 'PG1' | 'PG2' | 'SG1' | 'SG2' | 'SF1' | 'SF2' | 'PF1' | 'PF2' | 'C'

/** A single roster slot within a FanDuel lineup. */
export interface FDLineupSlot {
  /** Internal slot key (e.g. PG1, PG2, SG1 …). */
  slot: FDSlot
  /**
   * Full composite player ID as used in FD entry CSVs.
   * Format: "<slateId>-<playerId>"  e.g. "125392-157833"
   */
  fdPlayerId: string
  /** Display name resolved from the slate file (e.g. "Jalen Johnson"). */
  playerName: string
  /**
   * Whether this player's game has already started — slot cannot be swapped
   * and serves only as a structural anchor.
   */
  locked: boolean
}

/**
 * A complete FanDuel lineup entry.
 * Preserves all metadata from the original entry CSV so the export can
 * reproduce a valid FD bulk-upload CSV.
 */
export interface FDLineupEntry {
  entryId: string
  contestId: string
  contestName: string
  entryFee: number
  /** Exactly 9 slots in column order: PG, PG, SG, SG, SF, SF, PF, PF, C. */
  slots: FDLineupSlot[]
}

/**
 * Ordered slot keys that correspond to the 9 player columns in the FD
 * entry CSV (columns 4–12).
 */
export const FD_SLOT_ORDER: FDSlot[] = [
  'PG1', 'PG2',
  'SG1', 'SG2',
  'SF1', 'SF2',
  'PF1', 'PF2',
  'C',
]

/**
 * Human-readable column headers that appear in the FD upload CSV.
 * Note: duplicate position names are intentional (FanDuel format).
 */
export const FD_CSV_PLAYER_HEADERS = [
  'PG', 'PG', 'SG', 'SG', 'SF', 'SF', 'PF', 'PF', 'C',
] as const
