'use client'

import { useState, useEffect, useCallback } from 'react'
import { authFetch, getApiErrorMessage } from '@/lib/auth'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'
const fetch = authFetch

export interface SlateInfo {
  id: string
  platform: string
  sport: string
  date: string
  file_name: string
  created_at: string
  player_count: number | null
  status: string
  teams?: string[]
}

interface UseLatestSlateReturn {
  slates: SlateInfo[]
  selectedSlate: SlateInfo | null
  setSelectedId: (id: string) => void
  slateFile: File | null
  loading: boolean
  error: string | null
  reload: () => void
}

/**
 * Shared hook: fetches saved slates, auto-selects the latest,
 * and downloads its CSV as a File object for direct use in API calls.
 */
export function useLatestSlate(): UseLatestSlateReturn {
  const [slates, setSlates]             = useState<SlateInfo[]>([])
  const [selectedId, setSelectedId]     = useState<string | null>(null)
  const [slateFile, setSlateFile]       = useState<File | null>(null)
  const [loading, setLoading]           = useState(true)
  const [fileLoading, setFileLoading]   = useState(false)
  const [error, setError]               = useState<string | null>(null)

  const fetchSlates = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_BASE}/api/slates`)
      if (!res.ok) throw new Error(await getApiErrorMessage(res))
      const data = await res.json()
      const all: SlateInfo[] = data.slates ?? []
      setSlates(all)
      if (all.length > 0) {
        const latest = [...all].sort((a, b) =>
          (b.created_at ?? '').localeCompare(a.created_at ?? '')
        )[0]
        setSelectedId(prev => prev ?? latest.id)  // don't override manual selection
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Error loading slates')
    } finally {
      setLoading(false)
    }
  }, [])

  // Download the CSV file for the selected slate
  useEffect(() => {
    if (!selectedId) { setSlateFile(null); return }
    const slate = slates.find(s => s.id === selectedId)
    if (!slate) return

    setFileLoading(true)
    fetch(`${API_BASE}/api/slates/${selectedId}/download`)
      .then(r => {
        if (!r.ok) throw new Error(`Could not download slate CSV (${r.status})`)
        return r.blob()
      })
      .then(blob => {
        const file = new File([blob], slate.file_name, { type: 'text/csv' })
        setSlateFile(file)
      })
      .catch((err: unknown) => {
        setSlateFile(null)
        setError(err instanceof Error ? err.message : 'Could not download slate CSV')
      })
      .finally(() => setFileLoading(false))
  }, [selectedId, slates])

  useEffect(() => { fetchSlates() }, [fetchSlates])

  const selectedSlate = slates.find(s => s.id === selectedId) ?? null

  return {
    slates,
    selectedSlate,
    setSelectedId,
    slateFile,
    loading: loading || fileLoading,
    error,
    reload: fetchSlates,
  }
}
