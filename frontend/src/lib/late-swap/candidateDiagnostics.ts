// ---------------------------------------------------------------------------
// Candidate Pool Diagnostics
// Explains why a candidate is excluded, in user-facing language.
// ---------------------------------------------------------------------------

import { ExclusionReason, EXCLUSION_LABELS, type CandidatePoolItem } from './types'

export interface CandidateDiagnostic {
  reason: ExclusionReason
  label: string
  /** One-line explanation for tooltip / table cell */
  message: string
  /** Optional action the user can take */
  suggestion?: string
}

// ---------------------------------------------------------------------------
// Main function: get human-readable diagnostic for one candidate
// ---------------------------------------------------------------------------

export function getCandidateDiagnostic(item: CandidatePoolItem): CandidateDiagnostic | null {
  if (!item.reasonExcluded) return null

  const reason = item.reasonExcluded
  const label  = EXCLUSION_LABELS[reason]

  switch (reason) {
    case ExclusionReason.GAME_ALREADY_STARTED:
      return {
        reason, label,
        message:    `${item.name}'s game has already tipped off`,
        suggestion: 'Use a player from an upcoming game',
      }

    case ExclusionReason.SCRATCHED_OR_OUT:
      return {
        reason, label,
        message:    `${item.name} is on the scratch list`,
        suggestion: 'Remove from the scratch list if confirmed active',
      }

    case ExclusionReason.SALARY_TOO_HIGH:
      return {
        reason, label,
        message:    `${item.name} ($${item.salary.toLocaleString()}) exceeds remaining salary budget`,
        suggestion: 'Swap another player first to create salary room',
      }

    case ExclusionReason.POSITION_INELIGIBLE:
      return {
        reason, label,
        message:    `${item.name} (${item.positions.join('/')}) cannot fill any open roster slot`,
        suggestion: 'Check whether the scratched player shares a position',
      }

    case ExclusionReason.ALREADY_IN_LINEUP:
      return {
        reason, label,
        message:    `${item.name} is already in this lineup`,
        suggestion: 'Player is already rostered — no swap needed',
      }

    case ExclusionReason.USER_EXCLUDED:
      return {
        reason, label,
        message:    `${item.name} was manually excluded from the player pool`,
        suggestion: 'Click "Re-include" to restore this player as a candidate',
      }

    case ExclusionReason.NOT_IN_PLAYER_POOL:
      return {
        reason, label,
        message:    `${item.name} is not in the current player pool`,
        suggestion: 'Check the slate file and player pool filters',
      }

    case ExclusionReason.FAILED_STRATEGY_FILTER:
      return {
        reason, label,
        message:    `${item.name} was filtered out by a strategy rule`,
        suggestion: 'Review team / game stacking filters',
      }

    default:
      return {
        reason, label,
        message: `${item.name} is not eligible`,
      }
  }
}

// ---------------------------------------------------------------------------
// Batch variant for the whole pool
// ---------------------------------------------------------------------------

export function annotatePoolWithDiagnostics(
  items: CandidatePoolItem[],
): (CandidatePoolItem & { diagnostic: CandidateDiagnostic | null })[] {
  return items.map(item => ({
    ...item,
    diagnostic: getCandidateDiagnostic(item),
  }))
}

// ---------------------------------------------------------------------------
// Get badge style metadata for a candidate status
// ---------------------------------------------------------------------------

export type StatusBadge = {
  label: string
  bg: string
  color: string
}

export function getStatusBadge(item: CandidatePoolItem): StatusBadge {
  if (item.reasonExcluded === ExclusionReason.SCRATCHED_OR_OUT) {
    return { label: 'OUT',     bg: '#7f1d1d', color: '#fca5a5' }
  }
  if (item.reasonExcluded === ExclusionReason.GAME_ALREADY_STARTED) {
    return { label: 'STARTED', bg: '#292524', color: '#78716c' }
  }
  if (item.reasonExcluded === ExclusionReason.SALARY_TOO_HIGH) {
    return { label: '$ HIGH',  bg: '#431407', color: '#f97316' }
  }
  if (item.reasonExcluded === ExclusionReason.ALREADY_IN_LINEUP) {
    return { label: 'IN LU',   bg: '#1e3a5f', color: '#60a5fa' }
  }
  if (item.reasonExcluded === ExclusionReason.USER_EXCLUDED) {
    return { label: 'EXCL',    bg: '#450a0a', color: '#fca5a5' }
  }
  if (item.status === 'gtd') {
    return { label: 'GTD',     bg: '#78350f', color: '#fde68a' }
  }
  if (item.status === 'confirmed_starter') {
    return { label: 'ACTIVE',  bg: '#14532d', color: '#4ade80' }
  }
  if (item.eligible) {
    return { label: 'OK',      bg: '#14532d', color: '#4ade80' }
  }
  return   { label: 'EXCL',    bg: '#1e293b', color: '#64748b' }
}
