'use client'

/**
 * InjuryAlertBanner
 * =================
 * Displays a colour-coded alert banner when a slate has injured players.
 * Red for OUT, yellow for Questionable, orange for Doubtful.
 * Collapsible detail list with player names and reasons.
 */

import { useState } from 'react'
import { cn } from '@/lib/utils'
import type { InjurySummary } from '@/lib/api/slates'

interface InjuryAlertBannerProps {
  summary: InjurySummary
  className?: string
}

export function InjuryAlertBanner({ summary, className }: InjuryAlertBannerProps) {
  const [expanded, setExpanded] = useState(false)

  const hasOut = summary.out_count > 0
  const hasQ = summary.questionable_count > 0
  const hasD = summary.doubtful_count > 0
  const total = summary.out_count + summary.questionable_count + summary.doubtful_count
  const changed = summary.changed_since_export ?? 0

  if (total === 0) return null

  return (
    <div className={cn('rounded-lg border overflow-hidden', className)}>
      {/* OUT banner */}
      {hasOut && (
        <div className="bg-red-600/10 border-b border-red-600/20 px-4 py-2.5 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-red-600/20 text-red-400 text-[10px] font-bold">
              {summary.out_count}
            </span>
            <span className="text-red-400 text-sm font-semibold">
              Player{summary.out_count !== 1 ? 's' : ''} OUT
            </span>
            {changed > 0 && (
              <span className="text-[11px] text-red-300/80 bg-red-600/15 rounded px-1.5 py-0.5 font-medium">
                {changed} changed since export
              </span>
            )}
          </div>
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-[11px] text-red-300/70 hover:text-red-300 bg-transparent border-none cursor-pointer transition-colors"
          >
            {expanded ? 'Hide' : 'Details'}
          </button>
        </div>
      )}

      {/* Questionable banner */}
      {hasQ && (
        <div className="bg-yellow-600/10 border-b border-yellow-600/20 px-4 py-2 flex items-center gap-2">
          <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-yellow-600/20 text-yellow-400 text-[10px] font-bold">
            {summary.questionable_count}
          </span>
          <span className="text-yellow-400 text-sm font-semibold">Questionable</span>
        </div>
      )}

      {/* Doubtful banner */}
      {hasD && (
        <div className="bg-orange-600/10 border-b border-orange-600/20 px-4 py-2 flex items-center gap-2">
          <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-orange-600/20 text-orange-400 text-[10px] font-bold">
            {summary.doubtful_count}
          </span>
          <span className="text-orange-400 text-sm font-semibold">Doubtful</span>
        </div>
      )}

      {/* Expanded detail list */}
      {expanded && (
        <div className="bg-surface-base/50 px-4 py-3 space-y-1.5 max-h-[240px] overflow-y-auto">
          {summary.out_players.map((p, i) => (
            <div key={`out-${i}`} className="flex items-center gap-2 text-xs">
              <span className="px-1.5 py-0.5 rounded bg-red-600/20 text-red-400 border border-red-600/30 text-[10px] font-bold">OUT</span>
              <span className="text-text-primary font-semibold">{p.name}</span>
              <span className="text-text-muted">{p.team}</span>
              {p.detail && <span className="text-text-muted italic">— {p.detail}</span>}
            </div>
          ))}
          {summary.questionable_players.map((p, i) => (
            <div key={`q-${i}`} className="flex items-center gap-2 text-xs">
              <span className="px-1.5 py-0.5 rounded bg-yellow-600/20 text-yellow-400 border border-yellow-600/30 text-[10px] font-bold">Q</span>
              <span className="text-text-primary font-semibold">{p.name}</span>
              <span className="text-text-muted">{p.team}</span>
              {p.detail && <span className="text-text-muted italic">— {p.detail}</span>}
            </div>
          ))}
          {summary.doubtful_players.map((p, i) => (
            <div key={`d-${i}`} className="flex items-center gap-2 text-xs">
              <span className="px-1.5 py-0.5 rounded bg-orange-600/20 text-orange-400 border border-orange-600/30 text-[10px] font-bold">D</span>
              <span className="text-text-primary font-semibold">{p.name}</span>
              <span className="text-text-muted">{p.team}</span>
              {p.detail && <span className="text-text-muted italic">— {p.detail}</span>}
            </div>
          ))}
          {summary.last_updated && (
            <div className="text-[10px] text-text-muted pt-1 border-t border-surface-border/40">
              Last updated: {summary.last_updated}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
