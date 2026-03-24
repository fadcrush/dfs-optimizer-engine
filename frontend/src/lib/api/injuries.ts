/**
 * Injury Intelligence API client
 * ================================
 * Types and fetch helpers for /api/injuries/* endpoints.
 */

import { authFetch } from '@/lib/auth'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface InjuryScenario {
  scenario_name: string          // "OUT" | "IN_LIMITED" | "IN_FULL"
  probability: number
  expected_minutes: number
  usage_multiplier: number
  volatility_multiplier: number
}

export interface Beneficiary {
  player_id: string
  player_name: string
  position: string
  delta_minutes: number
  delta_usage: number
  delta_assist_rate: number
  delta_rebound_rate: number
  p_start: number
  p_close: number
  volatility_uplift: number
  confidence: number
  rank_score: number
  reason_codes: string[]
  scenario_name: string
}

export interface InjuryStatePlayer {
  player_id: string
  player_name: string
  team: string
  position: string
  status: string
  detail: string
  source: string
  event_classification: string
  p_play: number
  p_limited: number
  p_late_scratch: number
  p_start: number
  expected_minutes_low: number
  expected_minutes_mid: number
  expected_minutes_high: number
  confidence_score: number
  news_quality_score: number
  staleness_score: number
  market_impact_estimate: number
  last_event_at: string
  urgency: 'critical' | 'high' | 'moderate' | 'low'
  scenarios: InjuryScenario[]
  beneficiaries: Beneficiary[]
}

export interface InjurySlateBoard {
  injuries: InjuryStatePlayer[]
  count: number
  slate_date: string
  timestamp: string
}

export interface BeneficiaryRow extends Beneficiary {
  beneficiary_id: string
  injured_player_id: string
  injured_player_name: string
  injured_status: string
  injured_p_play: number
  team: string
  beneficiary_player_id: string
  beneficiary_name: string
  generated_at: string
}

export interface BeneficiariesResponse {
  beneficiaries: BeneficiaryRow[]
  count: number
}

export type OverrideAction = 'favor' | 'neutral' | 'fade' | 'exclude'

export interface InjuryOverride {
  override_id: string
  user_id: string
  slate_id: string
  player_id: string
  player_name: string
  action: OverrideAction
  projection_bump: number
  ownership_adjustment: number
  exclude_from_pool: boolean
  notes: string
  created_at: string
  updated_at: string
}

export interface OverridesResponse {
  overrides: InjuryOverride[]
  count: number
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

export async function loadInjurySlate(
  minImpact = 0.15,
  slateDate?: string,
): Promise<InjurySlateBoard> {
  const params = new URLSearchParams({ min_impact: String(minImpact) })
  if (slateDate) params.set('slate_date', slateDate)
  const res = await fetch(`${API_BASE}/api/injuries/slate?${params}`)
  if (!res.ok) throw new Error(`Injury slate fetch failed: ${res.status}`)
  return res.json() as Promise<InjurySlateBoard>
}

export async function loadBeneficiaries(
  playerId?: string,
  limit = 20,
): Promise<BeneficiariesResponse> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (playerId) params.set('player_id', playerId)
  const res = await fetch(`${API_BASE}/api/injuries/beneficiaries?${params}`)
  if (!res.ok) throw new Error(`Beneficiaries fetch failed: ${res.status}`)
  return res.json() as Promise<BeneficiariesResponse>
}

export async function setInjuryOverride(body: {
  player_id: string
  player_name?: string
  slate_id?: string
  action: OverrideAction
  projection_bump?: number
  ownership_adjustment?: number
  notes?: string
}): Promise<{ status: string; override_id: string; action: string }> {
  const res = await authFetch(`${API_BASE}/api/injuries/overrides`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`Override POST failed: ${res.status}`)
  return res.json()
}

export async function getInjuryOverrides(slateId?: string): Promise<OverridesResponse> {
  const params = slateId ? `?slate_id=${encodeURIComponent(slateId)}` : ''
  const res = await authFetch(`${API_BASE}/api/injuries/overrides${params}`)
  if (!res.ok) throw new Error(`Override GET failed: ${res.status}`)
  return res.json() as Promise<OverridesResponse>
}

export async function deleteInjuryOverride(
  playerId: string,
  slateId?: string,
): Promise<{ status: string }> {
  const params = slateId ? `?slate_id=${encodeURIComponent(slateId)}` : ''
  const res = await authFetch(
    `${API_BASE}/api/injuries/overrides/${encodeURIComponent(playerId)}${params}`,
    { method: 'DELETE' },
  )
  if (!res.ok) throw new Error(`Override DELETE failed: ${res.status}`)
  return res.json()
}
