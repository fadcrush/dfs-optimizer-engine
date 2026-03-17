export function MockDataBanner({ visible, message }: { visible?: boolean; message?: string }) {
  if (visible === false) return null
  return (
    <div
      role="alert"
      className="flex items-center gap-2 px-4 py-2.5 bg-warning-muted border-b border-warning/30 text-xs font-medium text-warning"
    >
      <span aria-hidden="true">⚠️</span>
      {message ?? 'Showing mock data — connect the backend to see live results'}
    </div>
  )
}
