/**
 * LockFadeControl
 * ===============
 * UI panel that lets the user lock players (force into every lineup)
 * or fade players (exclude from pool). Accepts the current projection
 * player list, emits `onLocksChange` / `onFadesChange` callbacks.
 *
 * Usage:
 *   <LockFadeControl
 *     players={projections}
 *     locks={locks}
 *     fades={fades}
 *     onLocksChange={setLocks}
 *     onFadesChange={setFades}
 *   />
 */

'use client';

import { useState, useMemo } from 'react';
import { cn } from '@/lib/utils';

export interface ProjectionPlayer {
  name: string;
  pos?: string;
  salary?: number;
  proj?: number;
  team?: string;
  injury_status?: string;
}

interface LockFadeControlProps {
  players: ProjectionPlayer[];
  locks: string[];
  fades: string[];
  onLocksChange: (locks: string[]) => void;
  onFadesChange: (fades: string[]) => void;
  className?: string;
}

export function LockFadeControl({
  players,
  locks,
  fades,
  onLocksChange,
  onFadesChange,
  className,
}: LockFadeControlProps) {
  const [search, setSearch] = useState('');
  const [activeTab, setActiveTab] = useState<'lock' | 'fade'>('lock');

  const locksSet = useMemo(() => new Set(locks.map((l) => l.toLowerCase())), [locks]);
  const fadesSet = useMemo(() => new Set(fades.map((f) => f.toLowerCase())), [fades]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    return players.filter((p) => !q || p.name.toLowerCase().includes(q)).slice(0, 50);
  }, [players, search]);

  const toggle = (name: string, action: 'lock' | 'fade') => {
    const low = name.toLowerCase();
    if (action === 'lock') {
      // Remove from fades if present
      const newFades = fades.filter((f) => f.toLowerCase() !== low);
      if (fadesSet.has(low)) onFadesChange(newFades);

      const newLocks = locksSet.has(low)
        ? locks.filter((l) => l.toLowerCase() !== low)
        : [...locks, name];
      onLocksChange(newLocks);
    } else {
      // Remove from locks if present
      const newLocks = locks.filter((l) => l.toLowerCase() !== low);
      if (locksSet.has(low)) onLocksChange(newLocks);

      const newFades = fadesSet.has(low)
        ? fades.filter((f) => f.toLowerCase() !== low)
        : [...fades, name];
      onFadesChange(newFades);
    }
  };

  const clearAll = () => {
    onLocksChange([]);
    onFadesChange([]);
  };

  return (
    <div className={cn('rounded-lg border border-surface-border bg-surface-raised p-4', className)}>
      {/* Header */}
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-text-primary">Lock / Exclude Players</h3>
        {(locks.length > 0 || fades.length > 0) && (
          <button
            type="button"
            onClick={clearAll}
            className="text-xs text-text-muted hover:text-danger"
          >
            Clear all
          </button>
        )}
      </div>

      {/* Tabs */}
      <div className="mb-3 flex gap-2">
        {(['lock', 'fade'] as const).map((tab) => (
          <button
            key={tab}
            type="button"
            onClick={() => setActiveTab(tab)}
            className={cn(
              'rounded px-3 py-1 text-xs font-medium transition-colors',
              activeTab === tab
                ? tab === 'lock'
                  ? 'bg-emerald-700 text-emerald-100'
                  : 'bg-red-800 text-red-100'
                : 'bg-surface-overlay text-text-muted hover:bg-surface-border',
            )}
          >
            {tab === 'lock'
              ? `🔒 Lock (${locks.length})`
              : `🚫 Exclude (${fades.length})`}
          </button>
        ))}
      </div>

      {/* Search */}
      <input
        type="text"
        placeholder="Search players…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="mb-2 w-full rounded border border-surface-border bg-surface-overlay px-3 py-1.5 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none"
      />

      {/* Player list */}
      <div className="max-h-56 overflow-y-auto space-y-1 pr-1">
        {filtered.length === 0 && (
          <p className="py-4 text-center text-xs text-text-muted">No players found</p>
        )}
        {filtered.map((player) => {
          const low = player.name.toLowerCase();
          const isLocked = locksSet.has(low);
          const isFaded = fadesSet.has(low);
          return (
            <div
              key={player.name}
              className={cn(
                'flex items-center justify-between rounded px-2 py-1.5 text-sm',
                isLocked && 'bg-emerald-900/30',
                isFaded && 'bg-red-900/30 opacity-60',
                !isLocked && !isFaded && 'hover:bg-surface-overlay',
              )}
            >
              <div className="flex items-center gap-2 min-w-0">
                {player.pos && (
                  <span className="text-xs font-mono text-text-muted w-5 shrink-0">
                    {player.pos}
                  </span>
                )}
                <span className="truncate text-text-primary">{player.name}</span>
                {player.injury_status && player.injury_status !== 'ACTIVE' && (
                  <span className="text-[10px] font-bold text-yellow-400">
                    {player.injury_status.slice(0, 1)}
                  </span>
                )}
              </div>
              <div className="flex gap-1 shrink-0">
                <button
                  type="button"
                  onClick={() => toggle(player.name, 'lock')}
                  title="Lock into every lineup"
                  className={cn(
                    'rounded px-1.5 py-0.5 text-[11px] font-medium transition-colors',
                    isLocked
                      ? 'bg-emerald-600 text-white'
                      : 'bg-surface-overlay text-text-muted hover:bg-success-muted hover:text-success',
                  )}
                >
                  🔒
                </button>
                <button
                  type="button"
                  onClick={() => toggle(player.name, 'fade')}
                  title="Exclude from pool"
                  className={cn(
                    'rounded px-1.5 py-0.5 text-[11px] font-medium transition-colors',
                    isFaded
                      ? 'bg-red-700 text-white'
                      : 'bg-surface-overlay text-text-muted hover:bg-danger-muted hover:text-danger',
                  )}
                >
                  🚫
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Active selections summary */}
      {(locks.length > 0 || fades.length > 0) && (
        <div className="mt-3 border-t border-surface-border pt-3 space-y-1">
          {locks.length > 0 && (
            <div className="flex flex-wrap gap-1">
              <span className="text-[10px] text-emerald-500 font-semibold mr-1">LOCKED:</span>
              {locks.map((l) => (
                <span
                  key={l}
                  className="inline-flex items-center gap-1 rounded bg-emerald-900/40 px-1.5 py-0.5 text-[11px] text-emerald-300"
                >
                  {l}
                  <button
                    type="button"
                    onClick={() => onLocksChange(locks.filter((x) => x !== l))}
                    className="text-emerald-500 hover:text-white"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          {fades.length > 0 && (
            <div className="flex flex-wrap gap-1">
              <span className="text-[10px] text-red-400 font-semibold mr-1">EXCLUDED:</span>
              {fades.map((f) => (
                <span
                  key={f}
                  className="inline-flex items-center gap-1 rounded bg-red-900/40 px-1.5 py-0.5 text-[11px] text-red-300"
                >
                  {f}
                  <button
                    type="button"
                    onClick={() => onFadesChange(fades.filter((x) => x !== f))}
                    className="text-red-400 hover:text-white"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
