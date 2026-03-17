import { cn } from '@/lib/utils'

type Variant = 'success' | 'warning' | 'danger' | 'neutral' | 'info'

const STYLES: Record<Variant, string> = {
  success: 'bg-success-muted text-success border-success/30',
  warning: 'bg-warning-muted text-warning border-warning/30',
  danger:  'bg-danger-muted text-danger border-danger/30',
  neutral: 'bg-surface-overlay text-text-secondary border-surface-border',
  info:    'bg-primary-muted text-primary border-primary/30',
}

export function StatusBadge({
  label,
  variant = 'neutral',
  dot = true,
  className,
}: {
  label: string
  variant?: Variant
  dot?: boolean
  className?: string
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-medium rounded-full border',
        STYLES[variant],
        className,
      )}
    >
      {dot && <span className="w-1.5 h-1.5 rounded-full bg-current" aria-hidden="true" />}
      {label}
    </span>
  )
}
