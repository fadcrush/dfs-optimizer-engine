'use client'
import { cn } from '@/lib/utils'

export type RunStep = 'idle' | 'fetching' | 'building' | 'running' | 'done' | 'error'

const STEP_LABELS: Record<RunStep, string> = {
  idle:     'Run Optimizer',
  fetching: 'Fetching projections…',
  building: 'Building pool…',
  running:  'Running optimizer…',
  done:     'Done',
  error:    'Error — retry',
}

export function RunOptimizerButton({
  step,
  onClick,
  disabled,
}: {
  step: RunStep
  onClick: () => void
  disabled?: boolean
}) {
  const busy = step === 'fetching' || step === 'building' || step === 'running'

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy || disabled}
      aria-busy={busy}
      className={cn(
        'w-full flex items-center justify-center gap-2 py-3 rounded-lg font-semibold text-sm',
        'transition-all duration-150',
        step === 'error'
          ? 'bg-danger text-white hover:bg-red-700'
          : 'bg-primary text-white hover:bg-primary-hover',
        (busy || disabled) && 'opacity-70 cursor-not-allowed',
      )}
    >
      {busy && (
        <span
          className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"
          aria-hidden="true"
        />
      )}
      {STEP_LABELS[step]}
    </button>
  )
}
