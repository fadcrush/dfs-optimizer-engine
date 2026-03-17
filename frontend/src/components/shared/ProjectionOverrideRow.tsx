/**
 * ProjectionOverrideRow
 * =====================
 * Renders an editable projection cell in the player table.
 * Click → become editable input → submit/blur → call onChange.
 *
 * Highlights the cell in amber when an override is active.
 *
 * Usage:
 *   <ProjectionOverrideRow
 *     playerName="LeBron James"
 *     baseProj={47.2}
 *     overrideValue={overrides['LeBron James']}
 *     onOverride={(name, val) => setOverrides({...overrides, [name]: val})}
 *   />
 */

'use client';

import { useState, useRef } from 'react';
import { cn } from '@/lib/utils';

interface ProjectionOverrideRowProps {
  playerName: string;
  baseProj: number;
  overrideValue?: number;
  onOverride: (playerName: string, value: number | undefined) => void;
  className?: string;
}

export function ProjectionOverrideRow({
  playerName,
  baseProj,
  overrideValue,
  onOverride,
  className,
}: ProjectionOverrideRowProps) {
  const [editing, setEditing] = useState(false);
  const [inputVal, setInputVal] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const displayValue = overrideValue !== undefined ? overrideValue : baseProj;
  const hasOverride = overrideValue !== undefined;

  const startEdit = () => {
    setInputVal(String(displayValue));
    setEditing(true);
    setTimeout(() => inputRef.current?.select(), 0);
  };

  const commitEdit = () => {
    const parsed = parseFloat(inputVal);
    if (!isNaN(parsed) && parsed >= 0) {
      if (Math.abs(parsed - baseProj) < 0.01) {
        // Same as base — remove override
        onOverride(playerName, undefined);
      } else {
        onOverride(playerName, parsed);
      }
    }
    setEditing(false);
  };

  const cancelEdit = () => {
    setEditing(false);
  };

  if (editing) {
    return (
      <input
        ref={inputRef}
        type="number"
        step="0.1"
        min="0"
        max="999"
        value={inputVal}
        onChange={(e) => setInputVal(e.target.value)}
        onBlur={commitEdit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commitEdit();
          if (e.key === 'Escape') cancelEdit();
        }}
        className={cn(
          'w-16 rounded border border-amber-500 bg-amber-950 px-1 py-0.5 text-right text-sm text-amber-200 focus:outline-none',
          className,
        )}
      />
    );
  }

  return (
    <button
      type="button"
      onClick={startEdit}
      title={hasOverride ? `Base: ${baseProj.toFixed(1)} — click to edit override` : 'Click to override projection'}
      className={cn(
        'w-16 rounded px-1 py-0.5 text-right text-sm transition-colors',
        hasOverride
          ? 'bg-amber-900/40 text-amber-300 font-semibold hover:bg-amber-800/50'
          : 'text-text-secondary hover:bg-surface-overlay',
        className,
      )}
    >
      {displayValue.toFixed(1)}
      {hasOverride && <span className="ml-0.5 text-[9px] text-amber-500">✎</span>}
    </button>
  );
}
