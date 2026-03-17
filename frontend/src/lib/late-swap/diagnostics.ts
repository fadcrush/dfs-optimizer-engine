// ---------------------------------------------------------------------------
// Late Swap — Failure Diagnostics Engine
// ---------------------------------------------------------------------------

import type { SwapResult } from '@/lib/api'
import {
  FailureReason,
  FAILURE_LABELS,
  FAILURE_SUGGESTIONS,
  type ExportValidationError,
} from './types'

export interface DiagnosticResult {
  reason: FailureReason
  label: string
  message: string
  suggestions: string[]
}

// ---------------------------------------------------------------------------
// classifySwapFailure
// Determines why a `lateSwap` returned zero viable candidates for a slot.
// ---------------------------------------------------------------------------

export function classifySwapFailure(swap: SwapResult): DiagnosticResult {
  const { candidates, scratched_position, salary_budget } = swap

  if (candidates.length === 0) {
    // Very low remaining budget → salary issue
    if (salary_budget < 4000) {
      const reason = FailureReason.SALARY_CAP_CONSTRAINT
      return {
        reason,
        label: FAILURE_LABELS[reason],
        message: `No eligible ${scratched_position} fits the remaining salary budget ($${salary_budget.toLocaleString()}).`,
        suggestions: FAILURE_SUGGESTIONS[reason],
      }
    }
    // No budget problem, but nobody found
    const reason = FailureReason.PLAYER_POOL_TOO_RESTRICTIVE
    return {
      reason,
      label: FAILURE_LABELS[reason],
      message: `No ${scratched_position} replacement candidates were found. The slate pool may be too restrictive.`,
      suggestions: FAILURE_SUGGESTIONS[reason],
    }
  }

  // Candidates exist but all have swap_score == 0 → all started
  if (candidates.every(c => c.swap_score === 0)) {
    const reason = FailureReason.ALL_CANDIDATES_STARTED
    return {
      reason,
      label: FAILURE_LABELS[reason],
      message: `All available ${scratched_position} candidates have already started their game and cannot be swapped in.`,
      suggestions: FAILURE_SUGGESTIONS[reason],
    }
  }

  // Generic
  const reason = FailureReason.NO_ELIGIBLE_REPLACEMENT
  return {
    reason,
    label: FAILURE_LABELS[reason],
    message: `No suitable ${scratched_position} replacement could be found with these constraints.`,
    suggestions: FAILURE_SUGGESTIONS[reason],
  }
}

// ---------------------------------------------------------------------------
// diagnoseFailedBatchSwap
// Used when batch log has a failed swap entry (replacement == null).
// ---------------------------------------------------------------------------

export function diagnoseFailedBatchSwap(
  scratched: string,
  position: string,
  salaryBudget: number,
): DiagnosticResult {
  if (salaryBudget > 0 && salaryBudget < 4000) {
    const reason = FailureReason.SALARY_CAP_CONSTRAINT
    return {
      reason,
      label: FAILURE_LABELS[reason],
      message: `No legal ${position} fits the remaining cap ($${salaryBudget.toLocaleString()}) after removing ${scratched}.`,
      suggestions: FAILURE_SUGGESTIONS[reason],
    }
  }

  const reason = FailureReason.NO_ELIGIBLE_REPLACEMENT
  return {
    reason,
    label: FAILURE_LABELS[reason],
    message: `Could not find a replacement for ${scratched} (${position}).`,
    suggestions: FAILURE_SUGGESTIONS[reason],
  }
}

// ---------------------------------------------------------------------------
// validateExportReadiness
// Pre-flight checks before exporting lineups.
// ---------------------------------------------------------------------------

export function validateExportReadiness(
  lineups: Array<{ players: string[]; label: string }>,
  scratchedNamesSet: Set<string>,
  salaryCap?: number,
): ExportValidationError[] {
  const errors: ExportValidationError[] = []

  for (const lu of lineups) {
    // Error: scratched players still in lineup
    const remaining = lu.players.filter(p => scratchedNamesSet.has(p.toLowerCase()))
    if (remaining.length > 0) {
      errors.push({
        lineup: lu.label,
        message: `Contains ${remaining.length} scratched player${remaining.length > 1 ? 's' : ''}: ${remaining.join(', ')}`,
        severity: 'error',
      })
    }

    // Warning: empty player slots
    const emptySlots = lu.players.filter(p => !p.trim()).length
    if (emptySlots > 0) {
      errors.push({
        lineup: lu.label,
        message: `Has ${emptySlots} empty slot${emptySlots > 1 ? 's' : ''}`,
        severity: 'warning',
      })
    }
  }

  return errors
}
