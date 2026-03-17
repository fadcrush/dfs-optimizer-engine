import { cn } from '@/lib/utils'
import type { ReactNode, HTMLAttributes } from 'react'

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode
  variant?: 'default' | 'raised' | 'bordered'
}

export function Card({ children, className, variant = 'default', ...props }: CardProps) {
  return (
    <div
      className={cn(
        'bg-surface-raised rounded-lg shadow-card',
        variant === 'bordered' && 'border border-surface-border',
        variant === 'raised' && 'bg-surface-overlay shadow-modal',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  )
}

export function CardHeader({
  title,
  icon,
  action,
}: {
  title: string
  icon?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="flex items-center justify-between px-4 py-3 border-b border-surface-border">
      <div className="flex items-center gap-2">
        {icon && <span className="text-primary w-4 h-4">{icon}</span>}
        <h2 className="text-sm font-semibold text-text-primary">{title}</h2>
      </div>
      {action}
    </div>
  )
}

export function CardBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('p-4', className)}>{children}</div>
}
