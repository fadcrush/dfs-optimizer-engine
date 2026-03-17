/**
 * InjuryBadge
 * ===========
 * Displays a colour-coded status badge for a player's injury status.
 * Uses the same status strings as the backend (OUT, QUESTIONABLE, DOUBTFUL, etc.)
 *
 * Usage:
 *   <InjuryBadge status="OUT" />
 *   <InjuryBadge status="QUESTIONABLE" detail="Knee" />
 */

import { cn } from '@/lib/utils';

export type InjuryStatus =
  | 'OUT'
  | 'DOUBTFUL'
  | 'QUESTIONABLE'
  | 'PROBABLE'
  | 'ACTIVE'
  | 'GTD'   // Game-time decision
  | string; // allow arbitrary strings from backend

const STATUS_STYLES: Record<string, string> = {
  OUT: 'bg-red-600/20 text-red-400 border-red-600/40',
  DOUBTFUL: 'bg-orange-600/20 text-orange-400 border-orange-600/40',
  QUESTIONABLE: 'bg-yellow-600/20 text-yellow-400 border-yellow-600/40',
  GTD: 'bg-yellow-600/20 text-yellow-400 border-yellow-600/40',
  PROBABLE: 'bg-green-700/20 text-green-400 border-green-600/40',
  ACTIVE: 'bg-emerald-700/20 text-emerald-400 border-emerald-600/40',
};

const DEFAULT_STYLE = 'bg-slate-700/20 text-slate-400 border-slate-600/40';

interface InjuryBadgeProps {
  status: InjuryStatus;
  detail?: string;
  className?: string;
  /** Show abbreviated single-char version for tight spaces */
  compact?: boolean;
}

const COMPACT_LABELS: Record<string, string> = {
  OUT: 'O',
  DOUBTFUL: 'D',
  QUESTIONABLE: 'Q',
  GTD: 'GTD',
  PROBABLE: 'P',
  ACTIVE: '✓',
};

export function InjuryBadge({
  status,
  detail,
  className,
  compact = false,
}: InjuryBadgeProps) {
  const upper = status.toUpperCase().trim();
  const style = STATUS_STYLES[upper] ?? DEFAULT_STYLE;
  const label = compact ? (COMPACT_LABELS[upper] ?? upper.slice(0, 1)) : upper;

  return (
    <span
      title={detail ? `${upper}: ${detail}` : upper}
      className={cn(
        'inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider',
        style,
        className,
      )}
    >
      {label}
    </span>
  );
}
