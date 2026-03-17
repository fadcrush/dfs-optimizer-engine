import { useMemo, useState } from 'react'
import { cn } from '@/lib/utils'

export type Column<T> = {
  key: keyof T | string
  header: string
  width?: string
  align?: 'left' | 'right' | 'center'
  sortable?: boolean
  render?: (value: unknown, row: T) => React.ReactNode
}

export function DataTable<T extends { [key: string]: unknown }>({
  columns,
  data,
  rowKey,
  searchable,
  searchPlaceholder,
  onRowClick,
  emptyMessage,
  selectable,
  onSelectionChange,
  maxHeight,
}: {
  columns: Column<T>[]
  data: T[]
  rowKey: (row: T) => string
  searchable?: boolean
  searchPlaceholder?: string
  onRowClick?: (row: T) => void
  emptyMessage?: string
  /** Enables leading checkbox column with bulk-select bar */
  selectable?: boolean
  onSelectionChange?: (keys: Set<string>) => void
  /** Sets a max-height (px) enabling vertical scroll with sticky header */
  maxHeight?: number
}) {
  const [query, setQuery] = useState('')
  const [sortKey, setSortKey] = useState<string | null>(null)
  const [sortAsc, setSortAsc] = useState(true)
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set())

  const filtered = useMemo(() => {
    const base = query
      ? data.filter((row) => JSON.stringify(row).toLowerCase().includes(query.toLowerCase()))
      : data
    if (!sortKey) return base
    return [...base].sort((a, b) => {
      const av = String(a[sortKey] ?? '')
      const bv = String(b[sortKey] ?? '')
      if (av < bv) return sortAsc ? -1 : 1
      if (av > bv) return sortAsc ? 1 : -1
      return 0
    })
  }, [data, query, sortKey, sortAsc])

  const allSelected = filtered.length > 0 && filtered.every((row) => selectedKeys.has(rowKey(row)))
  const someSelected = filtered.some((row) => selectedKeys.has(rowKey(row)))

  const toggleAll = () => {
    const keys = filtered.map(rowKey)
    const next = allSelected
      ? new Set([...selectedKeys].filter((k) => !keys.includes(k)))
      : new Set([...selectedKeys, ...keys])
    setSelectedKeys(next)
    onSelectionChange?.(next)
  }

  const toggleRow = (key: string) => {
    const next = new Set(selectedKeys)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    setSelectedKeys(next)
    onSelectionChange?.(next)
  }

  const clearSelection = () => {
    const empty = new Set<string>()
    setSelectedKeys(empty)
    onSelectionChange?.(empty)
  }

  return (
    <div>
      {/* Search */}
      {searchable && (
        <div className="mb-3">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={searchPlaceholder || 'Search...'}
            className="w-full px-3 py-2 bg-surface-overlay border border-surface-border rounded-md text-text-primary text-sm outline-none focus:border-primary placeholder:text-text-muted"
          />
        </div>
      )}

      {/* Bulk action bar */}
      {selectable && selectedKeys.size > 0 && (
        <div className="flex items-center gap-3 px-3 py-2 mb-2 bg-primary-muted border border-primary/30 rounded-lg text-sm animate-fadeIn">
          <span className="text-primary font-semibold">{selectedKeys.size} selected</span>
          <button
            onClick={clearSelection}
            className="text-text-muted hover:text-text-primary text-xs transition-colors cursor-pointer"
          >
            Clear
          </button>
        </div>
      )}

      {/* Table */}
      <div
        className="overflow-x-auto scrollbar-thin"
        style={maxHeight ? { maxHeight, overflowY: 'auto' } : undefined}
      >
        <table className="w-full text-sm border-collapse">
          <thead className="sticky top-0 z-10 bg-surface-overlay">
            <tr>
              {selectable && (
                <th className="w-9 px-2 py-2 border-b-2 border-surface-border">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    ref={(el) => { if (el) el.indeterminate = !allSelected && someSelected }}
                    onChange={toggleAll}
                    className="cursor-pointer accent-primary"
                    aria-label="Select all rows"
                  />
                </th>
              )}
              {columns.map((col) => {
                const isSorted = sortKey === String(col.key)
                return (
                  <th
                    key={String(col.key)}
                    className={cn(
                      'px-2.5 py-2 text-[11px] font-bold uppercase tracking-wide border-b-2 border-surface-border text-text-muted select-none whitespace-nowrap',
                      col.align === 'right' ? 'text-right' : col.align === 'center' ? 'text-center' : 'text-left',
                      col.sortable && 'cursor-pointer hover:text-text-primary transition-colors',
                    )}
                    style={{ width: col.width }}
                    onClick={() => {
                      if (!col.sortable) return
                      const key = String(col.key)
                      if (sortKey === key) setSortAsc(!sortAsc)
                      else { setSortKey(key); setSortAsc(true) }
                    }}
                  >
                    {col.header}
                    {col.sortable && (
                      <span className="ml-1 opacity-60 text-[10px]">
                        {isSorted ? (sortAsc ? '↑' : '↓') : '↕'}
                      </span>
                    )}
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length + (selectable ? 1 : 0)}
                  className="py-8 text-center text-text-muted"
                >
                  {emptyMessage || 'No data'}
                </td>
              </tr>
            ) : (
              filtered.map((row, i) => {
                const key = rowKey(row)
                const isSelected = selectedKeys.has(key)
                return (
                  <tr
                    key={key}
                    className={cn(
                      'border-b border-surface-border/40 transition-colors duration-75',
                      i % 2 === 0 ? 'bg-surface-raised' : 'bg-surface-base/30',
                      isSelected && '!bg-primary-muted',
                      (onRowClick || selectable) && 'cursor-pointer hover:bg-surface-overlay',
                    )}
                    onClick={() => onRowClick?.(row)}
                  >
                    {selectable && (
                      <td
                        className="w-9 px-2 py-2"
                        onClick={(e) => { e.stopPropagation(); toggleRow(key) }}
                      >
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleRow(key)}
                          className="cursor-pointer accent-primary"
                          aria-label={`Select row ${i + 1}`}
                        />
                      </td>
                    )}
                    {columns.map((col) => {
                      const value = row[col.key as keyof T]
                      return (
                        <td
                          key={String(col.key)}
                          className={cn(
                            'py-2 px-2.5 text-text-secondary text-sm',
                            col.align === 'right' ? 'text-right' : col.align === 'center' ? 'text-center' : 'text-left',
                          )}
                        >
                          {col.render ? col.render(value, row) : String(value ?? '')}
                        </td>
                      )
                    })}
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
