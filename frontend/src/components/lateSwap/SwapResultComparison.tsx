'use client'

import type { LineupSwapComparison } from '@/lib/late-swap/types'
import { FailureReason, FAILURE_LABELS, FAILURE_SUGGESTIONS } from '@/lib/late-swap/types'
import { formatSalary, cn } from '@/lib/utils'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function projColor(n: number) {
  if (n >= 3) return '#22c55e'
  if (n >= 0) return '#84cc16'
  if (n >= -3) return '#f97316'
  return '#ef4444'
}
function salaryColor(n: number) {
  // spending less = green (more budget), spending more = orange
  return n <= 0 ? '#22c55e' : '#f97316'
}
function sign(n: number) { return n >= 0 ? '+' : '' }

// ---------------------------------------------------------------------------
// Failure card
// ---------------------------------------------------------------------------

function FailureCard({ reason, message }: { reason: FailureReason; message: string }) {
  const suggestions = FAILURE_SUGGESTIONS[reason]
  return (
    <div className="bg-[#1f0a0a] border border-[#ef444444] rounded-lg px-4 py-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-base">⚠</span>
        <span className="text-xs font-bold text-danger uppercase">
          Swap Failed — {FAILURE_LABELS[reason]}
        </span>
      </div>
      <div className="text-xs text-[#fca5a5] mb-2.5">{message}</div>
      <div className="flex flex-col gap-1">
        {suggestions.map((s, i) => (
          <div key={i} className="text-[11px] text-text-muted flex items-start gap-1.5">
            <span className="text-[#475569] shrink-0">•</span>
            <span>{s}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SwapResultComparison
// ---------------------------------------------------------------------------

interface Props {
  comparison: LineupSwapComparison
  lineupLabel: string
}

export function SwapResultComparison({ comparison, lineupLabel }: Props) {
  const {
    diffs, finalSalary, originalSalary, salaryCap,
    totalProjDelta, totalSalaryDelta,
    successCount, failureCount,
    source, warnings,
  } = comparison

  const allFailed   = successCount === 0 && failureCount > 0
  const someSucceed = successCount > 0
  const statusColor = allFailed ? '#ef4444' : someSucceed ? '#22c55e' : '#94a3b8'
  const statusLabel = allFailed
    ? '✕ All Swaps Failed'
    : failureCount > 0
    ? `▲ Partial — ${successCount} swapped, ${failureCount} failed`
    : `✓ ${successCount} Swap${successCount !== 1 ? 's' : ''} Applied`

  return (
    <div
      className="bg-surface-base rounded-[10px] overflow-hidden mt-2"
      style={{ border: `1px solid ${allFailed ? '#ef444433' : '#1e293b'}` }}
    >
      {/* Header */}
      <div className="flex justify-between items-center px-4 py-2.5 border-b border-surface-border bg-[#0f172a]">
        <div>
          <span className="text-[11px] font-bold text-[#64748b] uppercase tracking-[0.05em]">
            Swap Result
          </span>
          <span className="text-[11px] text-[#475569] ml-2">{lineupLabel}</span>
        </div>
        <div className="flex items-center gap-2.5">
          <span
            className="text-[11px] font-bold px-2.5 py-0.5 rounded-lg"
            style={{ color: statusColor, background: allFailed ? '#2d0a0a' : someSucceed ? '#0d2718' : '#111' }}
          >
            {statusLabel}
          </span>
          <span className="text-[10px] text-[#334155]">{source}</span>
        </div>
      </div>

      {/* Player diffs */}
      <div className="p-4 flex flex-col gap-2.5">
        {diffs.map((d, i) => (
          <div key={i}
            className="rounded-lg px-3.5 py-2.5"
            style={{
              background: d.success ? '#0c1f17' : '#1f0a0a',
              border: `1px solid ${d.success ? '#22c55e22' : '#ef444422'}`,
            }}
          >
            {/* Removed → Added row */}
            <div className="flex items-center gap-2 flex-wrap">
              {/* Removed */}
              <div className="flex items-center gap-1.5">
                <span className="text-[13px] text-danger">➖</span>
                <span className="text-[13px] font-bold text-[#fca5a5]">{d.removed}</span>
                {d.position && (
                  <span className="text-[10px] bg-[#2d0a0a] text-danger px-[5px] py-px rounded-[3px] font-bold">{d.position}</span>
                )}
                {d.removedSalary != null && (
                  <span className="text-[11px] text-[#64748b]">{formatSalary(d.removedSalary)}</span>
                )}
              </div>

              <span className="text-sm text-[#334155]">→</span>

              {/* Added */}
              {d.added ? (
                <div className="flex items-center gap-1.5">
                  <span className="text-[13px] text-success">➕</span>
                  <span className="text-[13px] font-bold text-[#4ade80]">{d.added}</span>
                  {d.addedSalary != null && (
                    <span className="text-[11px] text-[#64748b]">{formatSalary(d.addedSalary)}</span>
                  )}
                </div>
              ) : (
                <div className="flex items-center gap-1.5">
                  <span className="text-xs">⚠</span>
                  <span className="text-xs text-danger italic">No replacement</span>
                </div>
              )}

              {/* Deltas */}
              <div className="ml-auto flex gap-3 items-center shrink-0">
                {d.success && (
                  <>
                    <div className="text-right">
                      <div className="text-[9px] text-[#475569] uppercase">FPTS</div>
                      <div className="text-xs font-bold" style={{ color: projColor(d.projDelta) }}>
                        {sign(d.projDelta)}{d.projDelta.toFixed(1)}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-[9px] text-[#475569] uppercase">Salary</div>
                      <div className="text-xs font-bold" style={{ color: salaryColor(d.salaryDelta) }}>
                        {sign(d.salaryDelta)}${Math.abs(d.salaryDelta).toLocaleString()}
                      </div>
                    </div>
                  </>
                )}
              </div>
            </div>

            {/* Failure card for this specific swap */}
            {!d.success && d.failureReason && (
              <div className="mt-2.5">
                <FailureCard reason={d.failureReason} message={d.failureMessage ?? `No replacement found for ${d.removed}.`} />
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Aggregate metrics row */}
      {someSucceed && (
        <div className="mx-4 mb-3.5 px-4 py-2.5 bg-[#111827] border border-surface-border rounded-lg flex gap-6 flex-wrap items-center">
          <div>
            <div className="text-[10px] text-[#475569] uppercase mb-0.5">Total FPTS Change</div>
            <div className="text-[18px] font-extrabold" style={{ color: projColor(totalProjDelta) }}>
              {sign(totalProjDelta)}{totalProjDelta.toFixed(1)}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-[#475569] uppercase mb-0.5">Salary: Original</div>
            <div className="text-[15px] font-bold text-text-muted">
              ${originalSalary.toLocaleString()}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-[#475569] uppercase mb-0.5">Salary: New</div>
            <div className="text-[15px] font-bold text-[#f1f5f9]">
              ${finalSalary.toLocaleString()}
              <span className="text-[11px] ml-1.5" style={{ color: salaryColor(totalSalaryDelta) }}>
                ({sign(totalSalaryDelta)}${Math.abs(totalSalaryDelta).toLocaleString()})
              </span>
            </div>
          </div>
          <div>
            <div className="text-[10px] text-[#475569] uppercase mb-0.5">Cap Remaining</div>
            <div
              className="text-[15px] font-bold"
              style={{ color: salaryCap - finalSalary < 1000 ? '#ef4444' : '#22c55e' }}
            >
              ${(salaryCap - finalSalary).toLocaleString()}
            </div>
          </div>
        </div>
      )}

      {/* Warnings */}
      {warnings.length > 0 && (
        <div className="mx-4 mb-3.5 px-3 py-2 bg-[#1c1900] border border-[#ca8a0422] rounded-md">
          {warnings.map((w, i) => (
            <div key={i} className="text-[11px] text-[#fde68a] flex gap-1.5">
              <span>⚠</span>{w}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
