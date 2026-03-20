import type { ReactNode } from 'react'

interface EmptyStateProps {
  /** Large icon or illustration placed above the title. Optional. */
  icon?: ReactNode
  /** Short headline, e.g. "No projections yet" */
  title: string
  /** One-line guidance, e.g. "Upload a salary CSV above to get started." */
  description?: string
  /** Optional call-to-action button. */
  action?: {
    label: string
    onClick: () => void
  }
  className?: string
}

export function EmptyState({ icon, title, description, action, className = '' }: EmptyStateProps) {
  return (
    <div
      className={`flex flex-col items-center justify-center py-16 px-6 text-center ${className}`}
      role="status"
      aria-label={title}
    >
      {icon && (
        <div className="mb-4 text-text-muted opacity-40 text-5xl leading-none select-none">
          {icon}
        </div>
      )}
      <p className="m-0 text-base font-semibold text-text-secondary">{title}</p>
      {description && (
        <p className="m-0 mt-1.5 text-sm text-text-muted max-w-[340px]">{description}</p>
      )}
      {action && (
        <button
          onClick={action.onClick}
          className="mt-5 bg-primary text-white border-0 rounded-lg px-5 py-2 text-sm font-semibold cursor-pointer hover:bg-primary-hover transition-colors"
        >
          {action.label}
        </button>
      )}
    </div>
  )
}
