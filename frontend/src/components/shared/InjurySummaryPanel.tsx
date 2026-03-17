'use client'

/**
 * InjurySummaryPanel
 * ==================
 * Collapsible panel showing all slate injuries grouped by team.
 * Includes a refresh button and last-updated timestamp.
 */

import { useState } from 'react'
import { cn } from '@/lib/utils'
import { InjuryBadge } from './InjuryBadge'
import type { InjurySummary, SlatePlayer } from '@/lib/api/slates'

interface InjurySummaryPanelProps {
  players: SlatePlayer[]
  summary: InjurySummary
  onRefresh?: () => void
  refreshing?: boolean
  className?: string
}

export function InjurySummaryPanel({
  players,
  summary,
  onRefresh,
  refreshing = false,
  className,
}: InjurySummaryPanelProps) {
  const [open, setOpen] = useState(false)

  const injuredPlayers = players.filter(
    p => p.injury_status && !['', 'ACTIVE', 'PROBABLE'].includes(p.injury_status.toUpperCase())
  )

  const totalInjured = injuredPlayers.length

  // Group by team
  const byTeam = new Map<string, SlatePlayer[]>()
  for (const p of injuredPlayers) {
    const team = p.team || 'Unknown'
    if (!byTeam.has(team)) byTeam.set(team, [])
    byTeam.get(team)!.push(p)
  }
  const sortedTeams = Array.from(byTeam.entries()).sort(([a], [b]) => a.localeCompare(b))

  if (totalInjured === 0 && summary.out_count === 0) return null

  return (
    <div className={cn('border border-surface-border rounded-xl overflow-hidden bg-surface-raised', className)}>
      {/* Header */}
      <div
        className="flex items-center justify-between px-4 py-3 cursor-pointer select-none hover:bg-surface-overlay transition-colors border-b border-surface-border"
        onClick={() => setOpen(o => !o)}
        role="button"
        aria-expanded={open}
      >
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-bold text-text-primary">Injury Report</span>
          {summary.out_count > 0 && (
            <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-red-600/15 border border-red-600/30 text-red-400">
              {summary.out_count} OUT
            </span>
          )}
          {summary.questionable_count > 0 && (
            <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-yellow-600/15 border border-yellow-600/30 text-yellow-400">
              {summary.questionable_count} Q
            </span>
          )}
          {summary.doubtful_count > 0 && (
            <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-orange-600/15 border border-orange-600/30 text-orange-400">
              {summary.doubtful_count} D
            </span>
          )}
          {(summary.changed_since_export ?? 0) > 0 && (
            <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-primary-muted border border-primary/30 text-primary">
              {summary.changed_since_export} changed
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {onRefresh && (
            <button
              onClick={e => { e.stopPropagation(); onRefresh() }}
              disabled={refreshing}
              className="px-2.5 py-1 rounded text-[11px] font-semibold bg-surface-overlay text-text-secondary border border-surface-border hover:text-text-primary transition-colors cursor-pointer disabled:opacity-50"
            >
              {refreshing ? 'Refreshing...' : 'Refresh'}
            </button>
          )}
          <span className="text-xs text-text-muted">{open ? '\u25B2' : '\u25BC'}</span>
        </div>
      </div>

      {/* Expanded content */}
      {open && (
        <div className="p-4 space-y-4 max-h-[400px] overflow-y-auto">
          {sortedTeams.map(([team, teamPlayers]) => (
            <div key={team}>
              <div className="text-[11px] font-bold uppercase text-text-muted mb-1.5 tracking-wide">
                {team} ({teamPlayers.length})
              </div>
              <div className="space-y-1">
                {teamPlayers
                  .sort((a, b) => {
                    const order: Record<string, number> = { OUT: 0, O: 0, DOUBTFUL: 1, D: 1, QUESTIONABLE: 2, Q: 2, GTD: 2 }
                    return (order[a.injury_status.toUpperCase()] ?? 9) - (order[b.injury_status.toUpperCase()] ?? 9)
                  })
                  .map((p, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs py-1 px-2 rounded hover:bg-surface-overlay transition-colors">
                      <InjuryBadge status={p.injury_status} compact />
                      <span className="text-text-primary font-semibold min-w-[120px]">{p.name}</span>
                      <span className="text-text-muted">{p.position}</span>
                      <span className="text-text-muted font-mono">${(p.salary ?? 0).toLocaleString()}</span>
                      {p.injury_detail && (
                        <span className="text-text-muted italic ml-auto truncate max-w-[200px]">{p.injury_detail}</span>
                      )}
                      {p.status_changed && (
                        <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-primary-muted text-primary border border-primary/30">NEW</span>
                      )}
                    </div>
                  ))}
              </div>
            </div>
          ))}

          {summary.last_updated && (
            <div className="text-[10px] text-text-muted pt-2 border-t border-surface-border/40">
              Report date: {summary.last_updated}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
