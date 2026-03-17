'use client'
import { useState } from 'react'
import { ChevronDown, ChevronRight, Copy } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatSalary } from '@/lib/utils'

interface LineupCardProps {
  index: number
  /** Map of slot key → player name, e.g. { PG: 'LeBron James', SG: '...', ... } */
  lineup: Record<string, string>
  /** Ordered slot keys for this site, e.g. ['PG','SG','SF','PF','C','G','F','UTIL'] */
  slots: ReadonlyArray<string>
  /** Aggregate projected points (computed by parent from lineup data) */
  projectedPoints?: number
  /** Aggregate salary (computed by parent from lineup data) */
  totalSalary?: number
  onCopy: () => void
  /** First 3 lineups default to expanded */
  defaultOpen?: boolean
}

export function LineupCard({
  index,
  lineup,
  slots,
  projectedPoints,
  totalSalary,
  onCopy,
  defaultOpen = false,
}: LineupCardProps) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div className="border border-surface-border rounded-lg overflow-hidden mb-2 bg-surface-raised">
      {/* Header */}
      <div
        className="flex items-center gap-2 px-3 py-2 bg-surface-overlay cursor-pointer hover:bg-surface-raised transition-colors duration-75"
        onClick={() => setOpen((o) => !o)}
        role="button"
        aria-expanded={open}
        aria-label={`Lineup ${index + 1}`}
      >
        {open
          ? <ChevronDown className="w-3.5 h-3.5 text-text-muted shrink-0" aria-hidden="true" />
          : <ChevronRight className="w-3.5 h-3.5 text-text-muted shrink-0" aria-hidden="true" />
        }
        <span className="text-xs font-semibold text-text-primary flex-1">
          Lineup {index + 1}
        </span>
        {totalSalary != null && (
          <span className="text-xs text-text-muted font-mono">
            {formatSalary(totalSalary)}
          </span>
        )}
        {projectedPoints != null && (
          <span className="text-xs text-success font-mono ml-2">
            {projectedPoints.toFixed(1)} pts
          </span>
        )}
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onCopy() }}
          aria-label={`Copy lineup ${index + 1} to clipboard`}
          className="p-1 rounded text-text-muted hover:text-text-primary hover:bg-surface-raised transition-colors"
        >
          <Copy className="w-3 h-3" />
        </button>
      </div>

      {/* Slot grid */}
      {open && (
        <div className="grid grid-cols-4 gap-px bg-surface-border">
          {slots.map((slot) => (
            <div key={slot} className="bg-surface-base px-2 py-1.5 flex flex-col">
              <span className="text-[9px] font-bold text-text-muted uppercase tracking-wider">
                {slot.replace('_2', '₂')}
              </span>
              <span
                className={cn(
                  'text-xs mt-0.5 truncate',
                  lineup[slot] ? 'text-text-primary' : 'text-text-muted italic',
                )}
                title={lineup[slot]}
              >
                {lineup[slot] ?? '—'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
