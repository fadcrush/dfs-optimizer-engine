'use client'
import { useEffect, useRef } from 'react'
import { Button } from './Button'

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel,
  variant = 'default',
  isLoading,
  onConfirm,
  onCancel,
}: {
  open: boolean
  title: string
  message: string
  confirmLabel: string
  variant?: 'danger' | 'default'
  isLoading?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const cancelRef = useRef<HTMLButtonElement>(null)

  // Auto-focus cancel button when dialog opens
  useEffect(() => {
    if (open) cancelRef.current?.focus()
  }, [open])

  // Escape key closes the dialog
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onCancel])

  if (!open) return null

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 animate-fadeIn"
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel()
      }}
    >
      <div className="bg-surface-overlay border border-surface-border rounded-xl shadow-modal w-full max-w-md p-5 animate-slideUp">
        <h3 id="confirm-dialog-title" className="text-base font-semibold text-text-primary">
          {title}
        </h3>
        <p className="text-sm text-text-secondary mt-2">{message}</p>
        <div className="flex gap-2 mt-5">
          <Button ref={cancelRef} variant="secondary" size="sm" className="flex-1" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            variant={variant === 'danger' ? 'danger' : 'primary'}
            size="sm"
            className="flex-1"
            isLoading={isLoading}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}

