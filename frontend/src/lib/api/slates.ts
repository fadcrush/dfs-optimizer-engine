import { authFetch, getApiErrorMessage } from '@/lib/auth'

// ── Types ───────────────────────────────────────────────────────────────────

export type SlateListItem = {
  id: string
  platform: string
  sport: string
  date: string
  lock_time: string | null
  slate_type: string
  player_count: number | null
  created_at: string
  status: string
}

export type SlatePlayer = {
  name: string
  position: string
  position_raw: string
  team: string
  opponent: string
  salary: number
  fppg: number
  value: number
  game: string
  injury: string
  // Live injury enrichment fields
  injury_status: string
  injury_detail: string
  injury_source: string   // "official_report" | "csv" | ""
  status_changed: boolean
}

export type InjuryPlayerRecord = {
  name: string
  team: string
  detail: string
  status: string
}

export type InjurySummary = {
  out_count: number
  questionable_count: number
  doubtful_count: number
  probable_count: number
  out_players: InjuryPlayerRecord[]
  questionable_players: InjuryPlayerRecord[]
  doubtful_players: InjuryPlayerRecord[]
  all_injuries: InjuryPlayerRecord[]
  changed_since_export: number
  last_updated: string | null
}

export type SlatePlayersResponse = {
  slate_id: string
  players: SlatePlayer[]
  total: number
  injury_summary: InjurySummary
}

// ── API client ──────────────────────────────────────────────────────────────

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'
const fetch = authFetch

export async function getSlates(): Promise<{ slates: SlateListItem[] }> {
  const res = await fetch(API_BASE + '/api/slates')
  if (!res.ok) {
    throw new Error(await getApiErrorMessage(res))
  }
  const body = await res.json()
  return { slates: body.slates ?? [] }
}

export async function uploadSlate(file: File, platform: string, sport: string) {
  const form = new FormData()
  form.append('file', file)

  const url = new URL(API_BASE + '/api/slates/upload')
  url.searchParams.set('platform', platform)
  url.searchParams.set('sport', sport)

  const res = await fetch(url.toString(), { method: 'POST', body: form })
  if (!res.ok) {
    return { success: false, error: await getApiErrorMessage(res) }
  }
  const body = await res.json()
  return { success: true, data: body }
}

export async function deleteSlate(id: string) {
  const res = await fetch(API_BASE + `/api/slates/${id}`, { method: 'DELETE' })
  if (!res.ok) {
    return { success: false, error: await getApiErrorMessage(res) }
  }
  return { success: true }
}

export async function getSlatePlayers(slateId: string): Promise<SlatePlayersResponse> {
  const res = await fetch(API_BASE + `/api/slates/${slateId}/players`)
  if (!res.ok) {
    throw new Error(await getApiErrorMessage(res))
  }
  return res.json()
}

export async function refreshInjuries(): Promise<{ status: string; message: string }> {
  const res = await fetch(API_BASE + '/api/injuries/refresh', { method: 'POST' })
  if (!res.ok) {
    throw new Error(await getApiErrorMessage(res))
  }
  return res.json()
}

export async function getInjurySummary(): Promise<{
  players: Array<{ player_name: string; status: string; detail: string; team: string; game_date: string }>
  total: number
  out_count: number
  questionable_count: number
  doubtful_count: number
  timestamp: string
}> {
  const res = await fetch(API_BASE + '/api/injuries/summary')
  if (!res.ok) {
    throw new Error(await getApiErrorMessage(res))
  }
  return res.json()
}
