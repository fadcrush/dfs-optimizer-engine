/**
 * ContestModeSelector
 * ===================
 * Displays 5 contest mode preset cards as a segmented control with Lucide icons.
 * All preset data and the onChange callback are preserved exactly.
 */

'use client';

import { cn } from '@/lib/utils';
import { DollarSign, Trophy, Target, User, Settings } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

export type ContestMode = 'cash' | 'gpp' | 'gpp_large' | 'single_entry' | 'custom';

export interface ContestModeConfig {
  mode: ContestMode;
  label: string;
  description: string;
  icon: string;
  defaults: {
    n_lineups: number;
    enable_stacking: boolean;
    min_game_stack: number;
    bring_back_count: number;
    max_from_team: number;
    max_from_game: number;
    max_exposure: number;
  };
}

export const CONTEST_MODE_PRESETS: ContestModeConfig[] = [
  {
    mode: 'cash',
    label: 'Cash',
    description: '50/50 · H2H · Double-up',
    icon: '💵',
    defaults: {
      n_lineups: 1,
      enable_stacking: false,
      min_game_stack: 0,
      bring_back_count: 0,
      max_from_team: 4,
      max_from_game: 6,
      max_exposure: 1.0,
    },
  },
  {
    mode: 'gpp',
    label: 'GPP',
    description: 'Small-mid tournaments',
    icon: '🏆',
    defaults: {
      n_lineups: 20,
      enable_stacking: true,
      min_game_stack: 2,
      bring_back_count: 1,
      max_from_team: 4,
      max_from_game: 5,
      max_exposure: 0.35,
    },
  },
  {
    mode: 'gpp_large',
    label: 'Large GPP',
    description: 'Millionaire-style contests',
    icon: '🎯',
    defaults: {
      n_lineups: 150,
      enable_stacking: true,
      min_game_stack: 3,
      bring_back_count: 2,
      max_from_team: 4,
      max_from_game: 5,
      max_exposure: 0.20,
    },
  },
  {
    mode: 'single_entry',
    label: 'Single Entry',
    description: 'One lineup, best shot',
    icon: '🎖️',
    defaults: {
      n_lineups: 1,
      enable_stacking: true,
      min_game_stack: 2,
      bring_back_count: 1,
      max_from_team: 4,
      max_from_game: 5,
      max_exposure: 1.0,
    },
  },
  {
    mode: 'custom',
    label: 'Custom',
    description: 'Configure manually',
    icon: '⚙️',
    defaults: {
      n_lineups: 20,
      enable_stacking: false,
      min_game_stack: 0,
      bring_back_count: 0,
      max_from_team: 8,
      max_from_game: 8,
      max_exposure: 0.6,
    },
  },
];

const MODE_ICONS: Record<ContestMode, LucideIcon> = {
  cash:         DollarSign,
  gpp:          Trophy,
  gpp_large:    Target,
  single_entry: User,
  custom:       Settings,
};

interface ContestModeSelectorProps {
  value: ContestMode;
  onChange: (mode: ContestMode, config: ContestModeConfig) => void;
  className?: string;
}

export function ContestModeSelector({ value, onChange, className }: ContestModeSelectorProps) {
  return (
    <div className={cn('space-y-2', className)}>
      <label className="text-xs font-semibold text-text-secondary uppercase tracking-wider">
        Contest Type
      </label>
      <div
        role="tablist"
        aria-label="Contest mode"
        className="flex rounded-lg bg-surface-base border border-surface-border overflow-hidden"
      >
        {CONTEST_MODE_PRESETS.map((preset) => {
          const active = value === preset.mode;
          const Icon = MODE_ICONS[preset.mode];
          return (
            <button
              key={preset.mode}
              role="tab"
              type="button"
              aria-selected={active}
              title={preset.description}
              onClick={() => onChange(preset.mode, preset)}
              className={cn(
                'relative flex-1 flex flex-col items-center gap-0.5 px-1.5 py-2.5 text-xs font-medium',
                'transition-colors duration-100 border-r border-surface-border last:border-0',
                active
                  ? 'bg-primary text-white'
                  : 'text-text-secondary hover:text-text-primary hover:bg-surface-raised',
              )}
            >
              <Icon className="w-3.5 h-3.5" aria-hidden="true" />
              <span className="leading-tight">{preset.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}




