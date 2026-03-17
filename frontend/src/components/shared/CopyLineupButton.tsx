/**
 * CopyLineupButton
 * ================
 * Copies a lineup (array of player names) to the clipboard as a
 * newline-separated list, plus optional FanDuel export format.
 *
 * Usage:
 *   <CopyLineupButton players={['LeBron James', 'Stephen Curry', ...]} />
 *
 * With FD positions:
 *   <CopyLineupButton
 *     players={[{ name: 'LeBron James', pos: 'SF', salary: 9800, proj: 52.3 }]}
 *     format="fanduel"
 *   />
 */

'use client';

import { useState } from 'react';
import { cn } from '@/lib/utils';

export interface LineupPlayer {
  name: string;
  pos?: string;
  salary?: number;
  proj?: number;
  own?: number;
}

type CopyFormat = 'names' | 'fanduel' | 'csv';

interface CopyLineupButtonProps {
  players: (string | LineupPlayer)[];
  format?: CopyFormat;
  lineupIndex?: number;
  className?: string;
}

function formatForCopy(players: (string | LineupPlayer)[], format: CopyFormat): string {
  const resolved: LineupPlayer[] = players.map((p) =>
    typeof p === 'string' ? { name: p } : p
  );

  if (format === 'names') {
    return resolved.map((p) => p.name).join('\n');
  }

  if (format === 'fanduel') {
    // FanDuel import: tab-separated player names (8 players)
    return resolved.map((p) => p.name).join('\t');
  }

  // CSV
  const header = 'Name,Pos,Salary,Proj,Own%';
  const rows = resolved.map(
    (p) =>
      `${p.name},${p.pos ?? ''},${p.salary ?? ''},${p.proj?.toFixed(1) ?? ''},${p.own?.toFixed(1) ?? ''}`
  );
  return [header, ...rows].join('\n');
}

export function CopyLineupButton({
  players,
  format = 'names',
  lineupIndex,
  className,
}: CopyLineupButtonProps) {
  const [state, setState] = useState<'idle' | 'copied' | 'error'>('idle');

  const handleCopy = async () => {
    try {
      const text = formatForCopy(players, format);
      await navigator.clipboard.writeText(text);
      setState('copied');
      setTimeout(() => setState('idle'), 2000);
    } catch {
      setState('error');
      setTimeout(() => setState('idle'), 2000);
    }
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      title={`Copy lineup${lineupIndex !== undefined ? ` #${lineupIndex + 1}` : ''}`}
      className={cn(
        'inline-flex items-center gap-1 rounded px-2 py-1 text-xs font-medium transition-colors',
        state === 'idle' && 'bg-surface-overlay text-text-secondary hover:bg-surface-border/60',
        state === 'copied' && 'bg-success-muted text-success',
        state === 'error' && 'bg-danger-muted text-danger',
        className,
      )}
    >
      {state === 'idle' && (
        <>
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="h-3.5 w-3.5"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
            />
          </svg>
          Copy
        </>
      )}
      {state === 'copied' && '✓ Copied!'}
      {state === 'error' && '✗ Failed'}
    </button>
  );
}
