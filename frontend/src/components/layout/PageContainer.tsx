import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export function PageContainer({
  title,
  description,
  action,
  breadcrumbs,
  children,
  className,
}: {
  title: string
  description?: string
  action?: ReactNode
  breadcrumbs?: { label: string; href?: string }[]
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('min-h-screen bg-surface-base text-text-primary p-4 md:p-6', className)}>
      <div className="max-w-7xl mx-auto glass-page rounded-[28px] px-5 py-5 md:px-8 md:py-8">
        {breadcrumbs && breadcrumbs.length > 0 && (
          <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-xs text-text-muted mb-3 glass-strip rounded-full px-3 py-1 w-fit">
            {breadcrumbs.map((crumb, i) => (
              <span key={i} className="flex items-center gap-1">
                {i > 0 && <span aria-hidden="true">/</span>}
                {crumb.href ? (
                  <a href={crumb.href} className="hover:text-text-primary transition-colors">
                    {crumb.label}
                  </a>
                ) : (
                  <span className="text-text-secondary">{crumb.label}</span>
                )}
              </span>
            ))}
          </nav>
        )}
        <div className="flex items-start justify-between gap-4 mb-6 flex-wrap">
          <div>
            <h1 className="display-title text-3xl text-text-primary">{title}</h1>
            {description && (
              <p className="text-sm text-text-secondary mt-1 max-w-2xl">{description}</p>
            )}
          </div>
          {action}
        </div>
        {children}
      </div>
    </div>
  )
}

