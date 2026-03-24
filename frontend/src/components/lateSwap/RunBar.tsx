'use client'

import { useState, useRef } from 'react'
import { cn } from '@/lib/utils'

export type ContestType = 'balanced' | 'cash' | 'gpp'

interface Props {
  lineupCount: number
  scratchedCount: number
  lockedCount: number
  /** Lineups containing a scratched player */
  affectedCount: number
  hasSlate: boolean
  slateName: string | null
  onSlateSelect: (f: File) => void
  loading: boolean
  batchError: string | null
  hasResults: boolean
  exportReady: boolean
  contestType: ContestType
  onContestTypeChange: (ct: ContestType) => void
  wProj: number
  wValue: number
  wOwn: number
  diversityFactor: number
  onWeightChange: (key: 'proj' | 'value' | 'own' | 'diversity', val: number) => void
  onRun: () => void
  onExport: () => void
}

export function RunBar({
  lineupCount,
  scratchedCount,
  lockedCount,
  affectedCount,
  hasSlate,
  slateName,
  onSlateSelect,
  loading,
  batchError,
  hasResults,
  exportReady,
  contestType,
  onContestTypeChange,
  wProj,
  wValue,
  wOwn,
  diversityFactor,
  onWeightChange,
  onRun,
  onExport,
}: Props) {
  const [showSettings, setShowSettings] = useState(false)
  const slateInputRef = useRef<HTMLInputElement>(null)

  const canRun = lineupCount > 0 && hasSlate && !loading
  const runTitle = !hasSlate
    ? 'Load a projection slate file first'
    : scratchedCount === 0
    ? 'No scratched players — mark players ❌ in the Player Controls above first'
    : ''

  return (
    <div className="sticky bottom-2 sm:bottom-3 z-50 px-2 sm:px-3">
      <div className="glass-panel-strong rounded-[24px] border border-surface-border/80 shadow-[0_18px_48px_rgba(2,6,23,0.55)] overflow-hidden">
      {/* ── Expanded settings ─────────────────────────────────────────────── */}
      {showSettings && (
        <div className="px-4 sm:px-5 py-4 border-b border-surface-border/80 bg-[rgba(7,14,24,0.5)] flex flex-wrap gap-6 items-end">
          {/* Contest type */}
          <div>
            <div className="section-label mb-2">Contest Type</div>
            <div className="flex gap-1">
              {(['balanced', 'cash', 'gpp'] as ContestType[]).map(ct => (
                <button
                  key={ct}
                  onClick={() => onContestTypeChange(ct)}
                  className={cn(
                    'px-3.5 py-1.5 rounded-full border text-[11px] font-semibold cursor-pointer transition-colors capitalize',
                    contestType === ct
                      ? 'bg-[#4d3a13] border-[#f4b540]/35 text-[#f4b540]'
                      : 'bg-transparent border-surface-border/60 text-text-muted hover:text-text-secondary',
                  )}
                >
                  {ct}
                </button>
              ))}
            </div>
          </div>

          {/* Scoring weights */}
          <div>
            <div className="section-label mb-2">Scoring Weights</div>
            <div className="flex items-center gap-4 flex-wrap">
              {(
                [
                  { key: 'proj' as const, label: 'Proj', val: wProj },
                  { key: 'value' as const, label: 'Value', val: wValue },
                  { key: 'own' as const, label: 'Own%', val: wOwn },
                ] as const
              ).map(w => (
                <div key={w.key} className="flex items-center gap-2">
                  <span className="text-[10px] text-text-muted w-8 text-right font-bold">{w.label}</span>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={w.val}
                    onChange={e => onWeightChange(w.key, parseFloat(e.target.value))}
                    className="w-20 accent-primary"
                  />
                  <span className="text-[11px] text-text-secondary w-8 font-mono">{w.val.toFixed(2)}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Diversity */}
          <div>
            <div className="section-label mb-2">
              Diversity Factor
              <span className="ml-1 text-text-muted normal-case font-normal tracking-normal">
                (prevents same player in every lineup)
              </span>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={diversityFactor}
                onChange={e => onWeightChange('diversity', parseFloat(e.target.value))}
                className="w-28 accent-primary"
              />
              <span className="text-[11px] text-text-secondary font-mono w-8">{diversityFactor.toFixed(2)}</span>
              <span className="text-[10px] text-text-muted">
                {diversityFactor <= 0.1 ? 'None' : diversityFactor <= 0.3 ? 'Low' : diversityFactor <= 0.6 ? 'Medium' : 'High'}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* ── Error banner ──────────────────────────────────────────────────── */}
      {batchError && (
        <div className="px-5 py-2.5 bg-[#40101a] border-b border-danger/30 text-[12px] text-[#fecdd3]">
          ⚠ {batchError}
        </div>
      )}

      {/* ── Main bar ──────────────────────────────────────────────────────── */}
      <div className="px-4 sm:px-5 py-4 flex items-center gap-3 flex-wrap min-h-[72px]">
        {/* Status chips */}
        <div className="flex items-center gap-2 flex-1 flex-wrap min-w-0">
          <span className="text-sm font-black text-text-primary tabular-nums tracking-tight">
            {lineupCount} lineup{lineupCount !== 1 ? 's' : ''}
          </span>
          {lockedCount > 0 && (
            <span className="text-[11px] font-bold bg-[#163654] text-[#7dd3fc] border border-[#7dd3fc]/20 px-3 py-1 rounded-full">
              🔒 {lockedCount} locked
            </span>
          )}
          {scratchedCount > 0 && (
            <span className="text-[11px] font-bold bg-[#5b1726] text-[#fecdd3] border border-danger/20 px-3 py-1 rounded-full">
              ❌ {scratchedCount} scratched
            </span>
          )}
          {affectedCount > 0 && (
            <span className="text-[11px] font-bold bg-[#4a3514] text-[#fbbf24] border border-[#fbbf24]/20 px-3 py-1 rounded-full">
              ⚠ {affectedCount} affected
            </span>
          )}
          {lineupCount > 0 && scratchedCount === 0 && (
            <span className="text-[11px] text-success bg-success-muted border border-success/20 px-3 py-1 rounded-full font-semibold">
              ✓ All clear
            </span>
          )}
        </div>

        {/* Right-side controls */}
        <div className="flex items-center gap-2 flex-wrap shrink-0 w-full sm:w-auto sm:justify-end">
          {/* Settings toggle */}
          <button
            onClick={() => setShowSettings(v => !v)}
            className={cn(
              'px-3 py-1.5 rounded-full border text-[11px] font-semibold cursor-pointer transition-colors',
              showSettings
                ? 'bg-[#4d3a13] border-[#f4b540]/35 text-[#f4b540]'
                : 'bg-transparent border-surface-border/60 text-text-muted hover:text-text-secondary',
            )}
          >
            ⚙ Settings
            {showSettings && (
              <span className="ml-1 text-[10px] opacity-60">
                {['balanced', 'cash', 'gpp'].includes(contestType) ? contestType : ''}
              </span>
            )}
          </button>

          {/* Slate file */}
          <input
            ref={slateInputRef}
            type="file"
            accept=".csv"
            onChange={e => e.target.files?.[0] && onSlateSelect(e.target.files[0])}
            className="hidden"
          />
          {!hasSlate ? (
            <button
              onClick={() => slateInputRef.current?.click()}
              className="px-3 py-1.5 rounded-full border border-[#f4b540]/25 bg-[#3a2a10] text-[#f4b540] text-[11px] font-bold cursor-pointer hover:bg-[#4a3514] transition-colors"
            >
              📂 Load Slate
            </button>
          ) : (
            <button
              onClick={() => slateInputRef.current?.click()}
              title="Click to change slate file"
              className="px-3 py-1.5 rounded-full border border-success/30 bg-success-muted text-success text-[11px] font-semibold cursor-pointer hover:bg-success/10 transition-colors max-w-[180px] truncate"
            >
              ✓ {slateName ?? 'Slate loaded'}
            </button>
          )}

          {/* Export button — visible once results are ready */}
          {hasResults && (
            <button
              disabled={!exportReady}
              onClick={onExport}
              className="px-4 py-2 rounded-full border border-success/30 bg-success-muted text-success text-sm font-bold cursor-pointer hover:bg-success/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex-1 sm:flex-none"
            >
              ↓ Export CSV
            </button>
          )}

          {/* Run button */}
          <button
            onClick={onRun}
            disabled={!canRun}
            title={runTitle}
            className={cn(
              'px-6 py-2.5 rounded-full text-sm font-black transition-colors min-w-[152px] text-center tracking-wide flex-1 sm:flex-none',
              canRun
                ? 'bg-primary border border-primary text-[#101722] hover:bg-primary-hover cursor-pointer shadow-[0_0_24px_rgba(244,181,64,0.22)]'
                : 'bg-surface-border text-text-muted border border-surface-border cursor-not-allowed',
            )}
          >
            {loading ? (
              <span className="flex items-center justify-center gap-2">
                <span className="inline-block w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Running…
              </span>
            ) : (
              '⚡ Batch Swap'
            )}
          </button>
        </div>
      </div>
      </div>
    </div>
  )
}
