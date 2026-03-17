'use client'

import type { SlateInfo } from '@/hooks/useLatestSlate'
import { useRouter } from 'next/navigation'

interface SlateSelectorProps {
  slates: SlateInfo[]
  selected: SlateInfo | null
  loading: boolean
  onSelect: (id: string) => void
  /** Called when user uploads a new file to override the saved slate */
  onFileOverride?: (file: File) => void
}

/**
 * Compact banner shown at the top of every analysis page.
 * Shows which slate is loaded (auto = latest), lets user switch,
 * and provides a "Upload different file" escape hatch.
 */
export function SlateSelector({ slates, selected, loading, onSelect, onFileOverride }: SlateSelectorProps) {
  const router = useRouter()

  if (loading) {
    return (
      <div className="flex items-center justify-between flex-wrap gap-2.5 bg-surface-base border border-surface-border rounded-lg px-4 py-2.5 mb-4">
        <div className="flex items-center gap-2.5">
          <div className="w-3.5 h-3.5 rounded-full border-2 border-surface-border border-t-primary animate-spin" />
          <span className="text-xs text-text-muted">Loading slate…</span>
        </div>
      </div>
    )
  }

  if (slates.length === 0) {
    return (
      <div className="flex items-center justify-between flex-wrap gap-2.5 bg-warning-muted border border-warning/30 rounded-lg px-4 py-2.5 mb-4">
        <span className="text-xs text-warning">⚠ No slate uploaded yet.</span>
        <button
          onClick={() => router.push('/slates')}
          className="inline-flex items-center gap-1 px-3 py-1 bg-warning/20 text-warning border border-warning/40 rounded text-[11px] font-semibold cursor-pointer hover:bg-warning/30 transition-colors"
        >
          Upload Slate →
        </button>
      </div>
    )
  }

  return (
    <div className="flex items-center justify-between flex-wrap gap-2.5 bg-surface-base border border-surface-border rounded-lg px-4 py-2.5 mb-4">
      <div className="flex items-center gap-2.5 flex-wrap flex-1">
        <span className="text-[11px] font-bold text-text-muted uppercase tracking-[0.06em]">
          Slate
        </span>

        {slates.length === 1 ? (
          <span className="text-xs text-text-primary font-semibold">
            {selected?.platform?.toUpperCase()} · {selected?.sport?.toUpperCase()} · {selected?.date}
          </span>
        ) : (
          <select
            value={selected?.id ?? ''}
            onChange={e => onSelect(e.target.value)}
            className="bg-surface-raised text-text-primary border border-surface-border rounded px-2 py-1 text-xs cursor-pointer outline-none focus:border-primary"
          >
            {slates
              .slice()
              .sort((a, b) => b.created_at.localeCompare(a.created_at))
              .map(s => (
                <option key={s.id} value={s.id}>
                  {s.platform.toUpperCase()} · {s.sport.toUpperCase()} · {s.date}
                  {s.player_count ? ` (${s.player_count} players)` : ''}
                </option>
              ))}
          </select>
        )}

        {selected && (
          <span className="text-[11px] text-success bg-success-muted rounded px-2 py-0.5 font-semibold">
            ✓ Ready
          </span>
        )}
      </div>

      <div className="flex gap-2 items-center shrink-0">
        <button
          onClick={() => router.push('/slates')}
          className="inline-flex items-center gap-1 px-3 py-1 bg-primary-muted text-primary border border-primary/30 rounded text-[11px] font-semibold cursor-pointer hover:bg-primary/20 transition-colors"
        >
          Manage Slates
        </button>
        {onFileOverride && (
          <label className="inline-flex items-center gap-1 px-3 py-1 bg-surface-overlay text-text-secondary border border-surface-border rounded text-[11px] font-semibold cursor-pointer hover:bg-surface-border transition-colors">
            Override File
            <input
              type="file"
              accept=".csv"
              className="hidden"
              onChange={e => {
                const f = e.target.files?.[0]
                if (f) onFileOverride(f)
              }}
            />
          </label>
        )}
      </div>
    </div>
  )
}
