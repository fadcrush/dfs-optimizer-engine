/**
 * DraftKings Late Swap — Lineup Model
 *
 * Represents a DraftKings entry-file lineup with full entry metadata and
 * slot-bound player IDs. Mirrors the FDLineupEntry model for FD.
 * Required to generate a valid DK bulk-upload CSV after late swap.
 */

/** The eight roster slots in a DraftKings NBA lineup. */
export type DKSlot = 'PG' | 'SG' | 'SF' | 'PF' | 'C' | 'G' | 'F' | 'UTIL'

/** A single roster slot within a DraftKings lineup. */
export interface DKLineupSlot {
  /** Slot label (e.g. 'PG', 'G', 'UTIL'). */
  slot: DKSlot
  /** Display name (e.g. "Victor Wembanyama"). */
  playerName: string
  /**
   * Numeric DraftKings player ID (e.g. "42210956").
   * Required for the "Name (ID)" format in the DK upload CSV.
   */
  playerId: string
}

/**
 * A complete DraftKings lineup entry.
 * Preserves all metadata from the original entry CSV so the export can
 * reproduce a valid DK bulk-upload CSV.
 */
export interface DKLineupEntry {
  entryId: string
  contestName: string
  contestId: string
  /** Entry fee as a string (e.g. "$1"). */
  entryFee: string
  /** Exactly 8 slots in column order: PG, SG, SF, PF, C, G, F, UTIL. */
  slots: DKLineupSlot[]
}

/**
 * Ordered slot keys corresponding to the 8 player columns in the DK
 * entry CSV (columns 4–11).
 */
export const DK_SLOT_ORDER: DKSlot[] = ['PG', 'SG', 'SF', 'PF', 'C', 'G', 'F', 'UTIL']
