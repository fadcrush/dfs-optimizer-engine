/**
 * Player Trends API client
 * =========================
 * Types and fetch helpers for /api/player-trends/* endpoints.
 *
 * All averages returned by these endpoints are HISTORICAL DESCRIPTIVE
 * STATISTICS — not projections.  Label them accordingly in the UI.
 */

import { authFetch } from '@/lib/auth'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PlayerSearchResult {
  player_name: string
  player_id:   string | null
  team:        string | null
}

/**
 * Aggregated averages and rates for a single time-window split
 * (last5 / last10 / season).
 */
export interface TrendSplit {
  games_used:         number
  avg_minutes:        number
  avg_points:         number
  avg_rebounds:       number
  avg_assists:        number
  avg_steals:         number
  avg_blocks:         number
  avg_turnovers:      number
  avg_three_pm:       number
  avg_fgm:            number
  avg_ftm:            number
  avg_dk_points:      number | null
  avg_fd_points:      number | null
  points_per_min:     number | null
  rebounds_per_min:   number | null
  assists_per_min:    number | null
  dk_points_per_min:  number | null
  fd_points_per_min:  number | null
  stddev_dk_points:   number | null
  stddev_fd_points:   number | null
}

/** One row in the recent game log table. */
export interface GameLogRow {
  game_id:         string | null
  game_date:       string
  opponent:        string | null
  is_home:         boolean | null
  wl:              string | null
  season:          string | null
  started:         boolean | null
  minutes:         number | null
  points:          number | null
  rebounds:        number | null
  assists:         number | null
  steals:          number | null
  blocks:          number | null
  turnovers:       number | null
  three_pointers:  number | null
  fg_made:         number | null
  ft_made:         number | null
  pf:              number | null
  dk_points:       number
  fd_points:       number
}

/** Full response from GET /api/player-trends/player */
export interface PlayerTrendsResult {
  player_name:           string
  player_id:             string | null
  team:                  string | null
  current_season:        string | null
  total_games_available: number
  last5:                 TrendSplit | null
  last10:                TrendSplit | null
  season:                TrendSplit | null
  recent_games:          GameLogRow[]
}

/** One row in the slate-wide table. */
export interface SlatePlayerTrend {
  player_name: string
  player_id:   string | null
  team:        string | null
  games_l10:   number
  avg_min:     number
  avg_pts:     number
  avg_reb:     number
  avg_ast:     number
  avg_stl:     number
  avg_blk:     number
  avg_tov:     number
  avg_3pm:     number
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

/**
 * Search for players by name substring.
 * Requires at least 2 characters in `q`.
 */
export async function searchPlayers(
  q: string,
  limit = 20,
): Promise<PlayerSearchResult[]> {
  if (q.trim().length < 2) return []
  const params = new URLSearchParams({ q: q.trim(), limit: String(limit) })
  const res = await authFetch(`${API_BASE}/api/player-trends/search?${params}`)
  if (!res.ok) return []
  const body = await res.json()
  return (body.players ?? []) as PlayerSearchResult[]
}

/**
 * Fetch the full trend profile for a player.
 * Returns null when the player is not found (404) or on any error.
 */
export async function getPlayerTrends(
  playerName: string,
  games = 10,
): Promise<PlayerTrendsResult | null> {
  if (!playerName.trim()) return null
  const params = new URLSearchParams({ name: playerName.trim(), games: String(games) })
  const res = await authFetch(`${API_BASE}/api/player-trends/player?${params}`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`Failed to load trends: ${res.status}`)
  return (await res.json()) as PlayerTrendsResult
}

/**
 * Fetch last-10 averages for all active players (slate-wide table).
 */
export async function getSlateTrends(limit = 200): Promise<SlatePlayerTrend[]> {
  const params = new URLSearchParams({ limit: String(limit) })
  const res = await authFetch(`${API_BASE}/api/player-trends/slate?${params}`)
  if (!res.ok) return []
  const body = await res.json()
  return (body.players ?? []) as SlatePlayerTrend[]
}
