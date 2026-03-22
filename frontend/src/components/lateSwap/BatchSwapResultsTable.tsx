'use client'

import { useMemo, useState } from 'react'
import type { BatchLateSwapResponse, BatchSwapLineupLog } from '@/lib/api'
import { formatSalary, cn } from '@/lib/utils'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function projDeltaColor(n: number) {
  if (n >= 3) return 'text-green-400'
  if (n >= 0) return 'text-lime-500'
  if (n >= -3) return 'text-orange-500'
  return 'text-red-500'
}
function sign(n: number) { return n >= 0 ? '+' : '' }

type StatusType = 'success' | 'partial' | 'failed' | 'clean'

function getLineupStatus(log: BatchSwapLineupLog): StatusType {
  if (log.swaps_made === 0 && log.swaps_failed === 0) return 'clean'
  if (log.swaps_failed === 0) return 'success'
  if (log.swaps_made > 0) return 'partial'
  return 'failed'
}

const STATUS_BADGE: Record<StatusType, { cls: string; text: string }> = {
  success: { cls: 'bg-[#14532d] text-[#4ade80]',  text: 'SUCCESS'  },
  partial: { cls: 'bg-[#78350f] text-[#fde68a]',  text: 'PARTIAL'  },
  failed:  { cls: 'bg-[#450a0a] text-[#fca5a5]',  text: 'FAILED'   },
  clean:   { cls: 'bg-[#0c2240] text-[#38bdf8]',  text: 'CLEAN'    },
}

const thCls = 'px-2.5 py-[7px] text-[10px] font-bold uppercase tracking-[0.05em] text-[#64748b] border-b-2 border-surface-border text-left whitespace-nowrap bg-[#0f172a] sticky top-0 cursor-pointer select-none'
const tdCls = 'px-2.5 py-[7px] text-xs border-b border-surface-border text-[#cbd5e1] whitespace-nowrap'

// ---------------------------------------------------------------------------
// BatchSwapResultsTable
// ---------------------------------------------------------------------------

interface Props {
  batchResult: BatchLateSwapResponse
  activeLineupIdx: number
  onSelectLineup: (idx: number) => void
  salaryCap: number
}

export function BatchSwapResultsTable({
  batchResult, activeLineupIdx, onSelectLineup, salaryCap
}: Props) {
  const [expanded, setExpanded] = useState(true)
  const [filter, setFilter] = useState<StatusType | 'all'>('all')

  const rows = useMemo(() => {
    return batchResult.swap_log.map(log => {
      const status = getLineupStatus(log)
      const totalProjDelta = log.swaps.reduce((sum, s) => sum + (s.proj_delta ?? 0), 0)
      return { log, status, totalProjDelta }
    })
  }, [batchResult])

  const counts = useMemo(() => {
    const c = { success: 0, partial: 0, failed: 0, clean: 0 }
    rows.forEach(r => c[r.status]++)
    return c
  }, [rows])

  const filtered = filter === 'all' ? rows : rows.filter(r => r.status === filter)

  const totalProjDelta = rows.reduce((s, r) => s + r.totalProjDelta, 0)
  const successRate = rows.length > 0
    ? Math.round((counts.success + counts.clean) / rows.length * 100)
    : 0

  return (
    <div className="bg-[#141b2d] border border-surface-border rounded-[10px] overflow-hidden mt-4">
      {/* Header */}
      <button
        onClick={() => setExpanded(o => !o)}
        className="w-full flex items-center justify-between px-[18px] py-3 bg-transparent border-none cursor-pointer text-left"
      >
        <div className="flex items-center gap-2.5 flex-wrap">
          <span className="text-xs font-bold text-text-muted uppercase tracking-[0.05em]">
            Batch Swap Results
          </span>
          <span className="text-[11px] text-[#475569]">
            {batchResult.total_lineups} lineup{batchResult.total_lineups !== 1 ? 's' : ''} · {batchResult.site}
          </span>
          {counts.success > 0 && (
            <span className="text-[11px] bg-[#14532d] text-[#4ade80] px-2 py-px rounded-lg font-bold">
              {counts.success} success
            </span>
          )}
          {counts.partial > 0 && (
            <span className="text-[11px] bg-[#78350f] text-[#fde68a] px-2 py-px rounded-lg font-bold">
              {counts.partial} partial
            </span>
          )}
          {counts.failed > 0 && (
            <span className="text-[11px] bg-[#450a0a] text-[#fca5a5] px-2 py-px rounded-lg font-bold">
              {counts.failed} failed
            </span>
          )}
          <span className={cn('text-[11px]', totalProjDelta >= 0 ? 'text-green-400' : 'text-red-500')}>
            Total FPTS: {sign(totalProjDelta)}{totalProjDelta.toFixed(1)}
          </span>
          <span className={cn('text-[11px]', successRate >= 80 ? 'text-green-400' : successRate >= 50 ? 'text-[#fde68a]' : 'text-[#fca5a5]')}>
            {successRate}% clean
          </span>
        </div>
        <span className={cn('text-[13px] text-[#64748b] shrink-0 inline-block transition-transform duration-200', expanded && 'rotate-180')}>▾</span>
      </button>

      {expanded && (
        <div className="border-t border-surface-border">
          {/* Filter bar */}
          <div className="flex gap-1.5 px-[18px] py-2.5 border-b border-surface-border items-center">
            <span className="text-[11px] text-[#475569]">Filter:</span>
            {(['all', 'success', 'partial', 'failed', 'clean'] as const).map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={cn('px-2.5 py-[3px] rounded-[5px] border-none text-[11px] font-semibold cursor-pointer uppercase tracking-[0.04em]',
                  filter === f ? 'bg-[#1e3a5f] text-blue-400' : 'bg-[#0f172a] text-[#475569]')}
              >
                {f === 'all' ? `All (${rows.length})` : `${f} (${counts[f] ?? 0})`}
              </button>
            ))}
          </div>

          {/* Table */}
          <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  {['Lineup', 'Status', 'Swapped', 'Failed', 'FPTS Δ', 'Salary', 'Cap Left', 'Swaps'].map(h => (
                    <th key={h} className={thCls}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map(({ log, status, totalProjDelta: tpd }) => {
                  // lineup_index is 1-based from backend; map to 0-based array index
                  const zeroIdx = log.lineup_index - 1
                  const isActive = zeroIdx === activeLineupIdx
                  const badge = STATUS_BADGE[status]
                  const capLeft = salaryCap - log.total_salary

                  return (
                    <tr
                      key={log.lineup_index}
                      onClick={() => onSelectLineup(zeroIdx)}
                      className={cn('cursor-pointer border-l-[3px] transition-colors duration-100',
                        isActive ? 'border-l-blue-500 bg-[#1e3a5f22]' : 'border-l-transparent bg-transparent')}
                    >
                      <td className={cn(tdCls, 'font-bold', isActive ? 'text-blue-400' : 'text-[#f1f5f9]')}>
                        Lineup {log.lineup_index}
                        {isActive && <span className="text-[9px] ml-[5px] text-[#3b82f6] font-bold">▶ active</span>}
                      </td>
                      <td className={tdCls}>
                        <span className={cn('text-[10px] font-bold px-2 py-0.5 rounded-[5px]', badge.cls)}>
                          {badge.text}
                        </span>
                      </td>
                      <td className={cn(tdCls, 'text-success font-bold')}>{log.swaps_made}</td>
                      <td className={cn(tdCls, log.swaps_failed > 0 ? 'text-[#fca5a5] font-bold' : 'text-[#475569]')}>
                        {log.swaps_failed}
                      </td>
                      <td className={cn(tdCls, 'font-bold', projDeltaColor(tpd))}>
                        {tpd !== 0 ? `${sign(tpd)}${tpd.toFixed(1)}` : '—'}
                      </td>
                      <td className={tdCls}>{formatSalary(log.total_salary)}</td>
                      <td className={cn(tdCls, capLeft < 1000 ? 'text-red-500' : capLeft < 3000 ? 'text-orange-500' : 'text-green-400')}>
                        ${capLeft.toLocaleString()}
                      </td>
                      <td className={cn(tdCls, 'text-[#475569]')}>
                        <div className="flex flex-wrap gap-[3px]">
                          {log.swaps.map((sw, j) => (
                            <span key={j} className={cn('text-[10px] px-[5px] py-px rounded-[3px]', sw.replacement ? 'bg-[#0a2318] text-[#86efac]' : 'bg-[#2d0a0a] text-[#fca5a5]')}>
                              {sw.scratched} → {sw.replacement ?? '—'}
                              {sw.replacement && sw.proj_delta != null && sw.proj_delta !== 0 && (
                                <span className={cn('ml-0.5', sw.proj_delta >= 0 ? 'text-green-400' : 'text-red-400')}>
                                  ({sign(sw.proj_delta)}{sw.proj_delta.toFixed(1)})
                                </span>
                              )}
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Failure details for any failed lineups */}
          {counts.failed > 0 && (
            <div className="px-[18px] py-2.5 border-t border-surface-border bg-[#0d0808]">
              <div className="text-[11px] font-bold text-danger mb-2">
                ⚠ {counts.failed} lineup{counts.failed > 1 ? 's' : ''} with unresolved scratched players
              </div>
              <div className="flex flex-col gap-[5px]">
                {rows.filter(r => r.status === 'failed').map(({ log }) => (
                  <div key={log.lineup_index} className="text-[11px] text-[#fca5a5] flex gap-2 flex-wrap">
                    <span className="font-bold">Lineup {log.lineup_index}:</span>
                    {log.swaps.filter(s => !s.replacement).map((sw, j) => (
                      <span key={j} className="text-[#f87171]">
                        ❌ No replacement for <strong>{sw.scratched}</strong>
                        {sw.note ? ` — ${sw.note}` : ''}
                      </span>
                    ))}
                  </div>
                ))}
              </div>
              <div className="mt-2 text-[11px] text-[#475569] flex flex-col gap-[3px]">
                <span>To resolve: click a failed lineup → adjust scratches → run Find Best Swaps manually</span>
                <span>Or: expand your player pool / remove salary constraints</span>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
