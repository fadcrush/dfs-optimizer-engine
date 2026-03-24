'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  Shield,
  ThumbsDown,
  ThumbsUp,
  Minus,
  XCircle,
  Activity,
  TrendingUp,
  Clock,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useInjuryEvents } from '@/hooks/useInjuryEvents'
import {
  loadInjurySlate,
  setInjuryOverride,
  getInjuryOverrides,
  deleteInjuryOverride,
  type InjuryStatePlayer,
  type Beneficiary,
  type InjuryOverride,
  type OverrideAction,
} from '@/lib/api/injuries'

// ─── Constants ────────────────────────────────────────────────────────────────

const URGENCY_STYLES: Record<string, string> = {
  critical: 'bg-red-900/40 text-red-300 border border-red-700/50',
  high:     'bg-orange-900/40 text-orange-300 border border-orange-700/50',
  moderate: 'bg-yellow-900/40 text-yellow-300 border border-yellow-700/50',
  low:      'bg-slate-800/60 text-slate-400 border border-slate-700/40',
}

const STATUS_COLOR: Record<string, string> = {
  OUT:        'text-red-400',
  DOUBTFUL:   'text-orange-400',
  GTD:        'text-yellow-400',
  QUESTIONABLE: 'text-yellow-300',
  PROBABLE:   'text-green-400',
  ACTIVE:     'text-emerald-400',
}

const ACTION_STYLES: Record<OverrideAction, { label: string; cls: string; icon: React.ReactNode }> = {
  favor:   { label: 'Favor',   cls: 'bg-emerald-700/40 text-emerald-300 border border-emerald-600/60 hover:bg-emerald-700/60', icon: <ThumbsUp className="w-3 h-3" /> },
  neutral: { label: 'Neutral', cls: 'bg-slate-700/40 text-slate-300 border border-slate-600/50 hover:bg-slate-700/60',         icon: <Minus className="w-3 h-3 " /> },
  fade:    { label: 'Fade',    cls: 'bg-orange-900/40 text-orange-300 border border-orange-700/50 hover:bg-orange-900/60',     icon: <ThumbsDown className="w-3 h-3" /> },
  exclude: { label: 'Exclude', cls: 'bg-red-900/40 text-red-300 border border-red-700/50 hover:bg-red-900/60',                 icon: <XCircle className="w-3 h-3" /> },
}

const REASON_LABELS: Record<string, string> = {
  likely_starter:            'Likely Starter',
  primary_backup_minutes:    'Primary Backup',
  backup_minutes:            'Backup Mins',
  same_position_overlap:     'Same Position',
  large_minutes_upside:      '↑↑ Minutes',
  moderate_minutes_upside:   '↑ Minutes',
  on_ball_usage_bump:        'Usage Bump',
  rebound_share_increase:    'Rebound↑',
  closing_lineup_boost:      'Closing Boost',
  high_confidence_beneficiary: 'High Conf',
}

// ─── Sub-components ──────────────────────────────────────────────────────────

function ProbabilityBar({ value, color = '#60a5fa' }: { value: number; color?: string }) {
  const pct = Math.min(100, Math.max(0, value * 100))
  return (
    <div className="relative h-1.5 w-full rounded-full bg-white/10 overflow-hidden">
      <div
        className="absolute left-0 top-0 h-full rounded-full transition-all duration-500"
        style={{ width: `${pct}%`, backgroundColor: color }}
      />
    </div>
  )
}

function ConfidenceDots({ score }: { score: number }) {
  const filled = Math.round(score * 5)
  return (
    <div className="flex gap-0.5">
      {Array.from({ length: 5 }).map((_, i) => (
        <span
          key={i}
          className={cn('w-1.5 h-1.5 rounded-full', i < filled ? 'bg-primary' : 'bg-white/15')}
        />
      ))}
    </div>
  )
}

function ReasonPill({ code }: { code: string }) {
  return (
    <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-medium bg-primary/20 text-primary border border-primary/30">
      {REASON_LABELS[code] ?? code}
    </span>
  )
}

// ─── Beneficiary card ────────────────────────────────────────────────────────

function BeneficiaryRow({
  ben,
  override,
  onOverride,
  pending,
}: {
  ben: Beneficiary
  override?: OverrideAction
  onOverride: (pid: string, name: string, action: OverrideAction) => void
  pending: boolean
}) {
  const currentAction: OverrideAction = override ?? 'neutral'

  return (
    <div className="flex flex-col gap-2 px-3 py-2 rounded-lg bg-white/5 border border-white/8">
      {/* Header row */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-white/10 text-text-muted uppercase shrink-0">
            {ben.position || '—'}
          </span>
          <span className="text-sm font-medium text-text-primary truncate">{ben.player_name}</span>
        </div>

        {/* Action buttons */}
        <div className="flex gap-1 shrink-0">
          {(['favor', 'neutral', 'fade', 'exclude'] as OverrideAction[]).map((a) => {
            const s = ACTION_STYLES[a]
            const active = currentAction === a
            return (
              <button
                key={a}
                onClick={() => !pending && onOverride(ben.player_id, ben.player_name, a)}
                disabled={pending}
                title={s.label}
                className={cn(
                  'flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium transition-all',
                  s.cls,
                  active && 'ring-1 ring-white/30 scale-105',
                  pending && 'opacity-50 cursor-not-allowed',
                )}
              >
                {s.icon}
                <span className="hidden sm:inline">{s.label}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-x-4 gap-y-1 text-xs text-text-muted">
        <div>
          <div className="text-[10px] uppercase tracking-wide mb-0.5">+Mins</div>
          <div className="text-text-secondary font-medium">+{ben.delta_minutes.toFixed(1)}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide mb-0.5">+Usage</div>
          <div className="text-text-secondary font-medium">+{(ben.delta_usage * 100).toFixed(1)}%</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide mb-0.5">P-Start</div>
          <div className="text-text-secondary font-medium">{(ben.p_start * 100).toFixed(0)}%</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide mb-0.5">Vol↑</div>
          <div className="text-text-secondary font-medium">×{ben.volatility_uplift.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide mb-0.5">Confidence</div>
          <ConfidenceDots score={ben.confidence} />
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide mb-0.5">Scenario</div>
          <div className="text-text-secondary font-medium text-[11px]">{ben.scenario_name}</div>
        </div>
      </div>

      {/* Reason codes */}
      {ben.reason_codes.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {ben.reason_codes.map((rc) => (
            <ReasonPill key={rc} code={rc} />
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Injury card ─────────────────────────────────────────────────────────────

function InjuryCard({
  player,
  overrides,
  onOverride,
  pendingSet,
}: {
  player: InjuryStatePlayer
  overrides: Record<string, OverrideAction>
  onOverride: (pid: string, name: string, action: OverrideAction) => void
  pendingSet: Set<string>
}) {
  const [expanded, setExpanded] = useState(false)
  const pOut = Math.max(0, 1 - player.p_play)
  const statusCls = STATUS_COLOR[player.status] ?? 'text-text-muted'
  const urgencyCls = URGENCY_STYLES[player.urgency] ?? URGENCY_STYLES.low

  return (
    <div className={cn('glass-panel rounded-xl overflow-hidden', player.urgency === 'critical' && 'border border-red-700/40')}>
      {/* Card header */}
      <div className="flex items-start justify-between gap-3 p-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <span className={cn('text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wider', urgencyCls)}>
              {player.urgency}
            </span>
            <span className={cn('text-sm font-bold uppercase', statusCls)}>{player.status}</span>
            {player.position && (
              <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-white/10 text-text-muted uppercase">
                {player.position}
              </span>
            )}
          </div>
          <div className="text-base font-semibold text-text-primary">{player.player_name}</div>
          <div className="text-xs text-text-muted mt-0.5">{player.team}</div>
          {player.detail && (
            <div className="text-xs text-text-secondary mt-1 leading-relaxed line-clamp-2">
              {player.detail}
            </div>
          )}
        </div>

        {/* Probability snapshot */}
        <div className="flex flex-col items-end gap-1 shrink-0 text-right">
          <div className="text-lg font-bold text-text-primary">{(pOut * 100).toFixed(0)}%</div>
          <div className="text-[10px] text-text-muted uppercase tracking-wide">Out prob.</div>
          <ConfidenceDots score={player.confidence_score} />
        </div>
      </div>

      {/* Probability bars */}
      <div className="px-4 pb-3 grid grid-cols-3 gap-3">
        <div>
          <div className="flex justify-between text-[10px] text-text-muted mb-1">
            <span>OUT</span><span>{(pOut * 100).toFixed(0)}%</span>
          </div>
          <ProbabilityBar value={pOut} color="#f87171" />
        </div>
        <div>
          <div className="flex justify-between text-[10px] text-text-muted mb-1">
            <span>Limited</span><span>{(player.p_limited * 100).toFixed(0)}%</span>
          </div>
          <ProbabilityBar value={player.p_limited} color="#fb923c" />
        </div>
        <div>
          <div className="flex justify-between text-[10px] text-text-muted mb-1">
            <span>Late-Scratch</span><span>{(player.p_late_scratch * 100).toFixed(0)}%</span>
          </div>
          <ProbabilityBar value={player.p_late_scratch} color="#facc15" />
        </div>
      </div>

      {/* Minutes band */}
      <div className="px-4 pb-3 flex items-center gap-2 text-xs text-text-muted border-t border-white/8 pt-3">
        <Clock className="w-3 h-3 shrink-0" />
        <span>
          Mins: {player.expected_minutes_low.toFixed(0)} –{' '}
          {player.expected_minutes_mid.toFixed(0)} –{' '}
          {player.expected_minutes_high.toFixed(0)}
        </span>
        {player.source && (
          <>
            <span className="text-white/20">·</span>
            <span className="truncate">{player.source}</span>
          </>
        )}
        {player.last_event_at && (
          <>
            <span className="text-white/20 ml-auto">updated</span>
            <span className="shrink-0">{new Date(player.last_event_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
          </>
        )}
      </div>

      {/* Expand beneficiaries */}
      {player.beneficiaries.length > 0 && (
        <>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="w-full flex items-center justify-between px-4 py-2.5 text-xs font-medium text-text-secondary border-t border-white/8 hover:bg-white/5 transition-colors"
          >
            <span className="flex items-center gap-1.5">
              <TrendingUp className="w-3.5 h-3.5" />
              {player.beneficiaries.length} Beneficiar{player.beneficiaries.length === 1 ? 'y' : 'ies'}
            </span>
            {expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>

          {expanded && (
            <div className="px-4 pb-4 flex flex-col gap-2">
              {player.beneficiaries.map((b) => (
                <BeneficiaryRow
                  key={b.player_id}
                  ben={b}
                  override={overrides[b.player_id]}
                  onOverride={onOverride}
                  pending={pendingSet.has(b.player_id)}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ─── Override summary ─────────────────────────────────────────────────────────

function OverrideSummary({
  overrides,
  onRemove,
}: {
  overrides: InjuryOverride[]
  onRemove: (playerId: string) => void
}) {
  if (!overrides.length) return null

  const favored  = overrides.filter((o) => o.action === 'favor')
  const faded    = overrides.filter((o) => o.action === 'fade')
  const excluded = overrides.filter((o) => o.action === 'exclude')

  return (
    <div className="glass-strip rounded-xl p-4 border border-white/10">
      <div className="flex items-center gap-2 mb-3">
        <Shield className="w-4 h-4 text-primary" />
        <span className="text-sm font-semibold text-text-primary">Override Summary</span>
        <span className="ml-auto text-xs text-text-muted">{overrides.length} active</span>
      </div>

      <div className="grid grid-cols-3 gap-3 mb-3">
        <div className="text-center">
          <div className="text-xl font-bold text-emerald-400">{favored.length}</div>
          <div className="text-[10px] text-text-muted uppercase tracking-wide">Favored</div>
        </div>
        <div className="text-center">
          <div className="text-xl font-bold text-orange-400">{faded.length}</div>
          <div className="text-[10px] text-text-muted uppercase tracking-wide">Faded</div>
        </div>
        <div className="text-center">
          <div className="text-xl font-bold text-red-400">{excluded.length}</div>
          <div className="text-[10px] text-text-muted uppercase tracking-wide">Excluded</div>
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        {overrides.map((o) => {
          const s = ACTION_STYLES[o.action as OverrideAction]
          return (
            <div key={o.override_id} className="flex items-center justify-between gap-2 px-2 py-1 rounded bg-white/5">
              <div className="flex items-center gap-2 min-w-0">
                <span className={cn('text-[10px] px-1.5 py-0.5 rounded font-medium', s.cls)}>{s.label}</span>
                <span className="text-xs text-text-secondary truncate">{o.player_name || o.player_id}</span>
              </div>
              <div className="flex items-center gap-3 shrink-0 text-xs text-text-muted">
                {o.projection_bump !== 0 && (
                  <span className={o.projection_bump > 0 ? 'text-emerald-400' : 'text-red-400'}>
                    {o.projection_bump > 0 ? '+' : ''}{(o.projection_bump * 100).toFixed(0)}% proj
                  </span>
                )}
                <button
                  onClick={() => onRemove(o.player_id)}
                  className="text-text-muted hover:text-red-400 transition-colors"
                  title="Remove override"
                >
                  <XCircle className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ─── Page ────────────────────────────────────────────────────────────────────

export default function InjuriesPage() {
  const [board, setBoard] = useState<InjuryStatePlayer[]>([])
  const [overrideMap, setOverrideMap] = useState<Record<string, OverrideAction>>({})
  const [activeOverrides, setActiveOverrides] = useState<InjuryOverride[]>([])
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'critical' | 'high' | 'moderate'>('all')
  const [minImpact, setMinImpact] = useState(0.15)
  const { players: ssePlayers, lastUpdated } = useInjuryEvents()
  const lastSseCount = useRef(0)

  const fetchBoard = useCallback(async () => {
    try {
      const data = await loadInjurySlate(minImpact)
      setBoard(data.injuries)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load injury board')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [minImpact])

  const fetchOverrides = useCallback(async () => {
    try {
      const data = await getInjuryOverrides()
      setActiveOverrides(data.overrides)
      const map: Record<string, OverrideAction> = {}
      for (const o of data.overrides) {
        map[o.player_id] = o.action as OverrideAction
      }
      setOverrideMap(map)
    } catch {
      // non-critical, overrides are optional
    }
  }, [])

  useEffect(() => {
    fetchBoard()
    fetchOverrides()
  }, [fetchBoard, fetchOverrides])

  // Re-fetch when SSE detects new injury updates
  useEffect(() => {
    if (ssePlayers.length !== lastSseCount.current) {
      lastSseCount.current = ssePlayers.length
      if (!loading) fetchBoard()
    }
  }, [ssePlayers, loading, fetchBoard])

  const handleRefresh = () => {
    setRefreshing(true)
    fetchBoard()
  }

  const handleOverride = async (pid: string, name: string, action: OverrideAction) => {
    setPendingIds((s) => new Set(s).add(pid))
    try {
      await setInjuryOverride({ player_id: pid, player_name: name, action })
      setOverrideMap((prev) => {
        const next = { ...prev }
        if (action === 'neutral') delete next[pid]
        else next[pid] = action
        return next
      })
      await fetchOverrides()
    } catch {
      // silently ignore — user can retry
    } finally {
      setPendingIds((s) => { const n = new Set(s); n.delete(pid); return n })
    }
  }

  const handleRemoveOverride = async (pid: string) => {
    try {
      await deleteInjuryOverride(pid)
      await fetchOverrides()
    } catch {
      // silently ignore
    }
  }

  const filtered = board.filter((p) => {
    if (filter === 'all') return true
    return p.urgency === filter
  })

  const urgencyCounts = {
    critical: board.filter((p) => p.urgency === 'critical').length,
    high:     board.filter((p) => p.urgency === 'high').length,
    moderate: board.filter((p) => p.urgency === 'moderate').length,
    low:      board.filter((p) => p.urgency === 'low').length,
  }

  return (
    <div className="min-h-screen surface-base px-4 py-6 md:px-8">
      {/* ── Header ── */}
      <div className="flex items-center justify-between flex-wrap gap-3 mb-6">
        <div className="flex items-center gap-3">
          <AlertTriangle className="w-6 h-6 text-yellow-400" />
          <div>
            <h1 className="text-xl font-bold text-text-primary">Injury Intelligence</h1>
            <p className="text-xs text-text-muted">
              Probabilistic injury states · Beneficiary uplift · Override controls
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {lastUpdated && (
            <span className="text-[10px] text-text-muted hidden sm:block">
              SSE: {new Date(lastUpdated).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          )}
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-white/10 text-text-secondary hover:bg-white/15 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', refreshing && 'animate-spin')} />
            Refresh
          </button>
        </div>
      </div>

      {/* ── Impact threshold ── */}
      <div className="glass-strip rounded-xl px-4 py-3 mb-4 flex items-center gap-4 flex-wrap">
        <span className="text-xs text-text-muted">Min impact threshold:</span>
        {[0.05, 0.15, 0.30, 0.50].map((v) => (
          <button
            key={v}
            onClick={() => setMinImpact(v)}
            className={cn(
              'px-2.5 py-1 rounded text-xs font-medium transition-all',
              minImpact === v
                ? 'bg-primary text-white'
                : 'bg-white/8 text-text-muted hover:bg-white/15',
            )}
          >
            {(v * 100).toFixed(0)}%+
          </button>
        ))}
        <span className="ml-auto text-xs text-text-muted">{board.length} players tracked</span>
      </div>

      {/* ── Urgency filter tabs ── */}
      <div className="flex gap-2 mb-5 flex-wrap">
        {(['all', 'critical', 'high', 'moderate'] as const).map((f) => {
          const count = f === 'all' ? board.length : urgencyCounts[f]
          const colors: Record<string, string> = {
            all:      'bg-white/10 text-text-secondary',
            critical: 'bg-red-900/50 text-red-300',
            high:     'bg-orange-900/50 text-orange-300',
            moderate: 'bg-yellow-900/50 text-yellow-300',
          }
          return (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all border',
                filter === f ? colors[f] + ' border-white/20 scale-105' : 'border-transparent text-text-muted hover:bg-white/8',
              )}
            >
              <span className="capitalize">{f}</span>
              <span className="px-1.5 py-0.5 rounded-full bg-white/10 text-[10px]">{count}</span>
            </button>
          )
        })}
      </div>

      {error && (
        <div className="glass-panel rounded-xl px-4 py-3 mb-4 text-sm text-red-400 flex items-center gap-2 border border-red-700/40">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      )}

      {/* ── Main layout: Board + Sidebar ── */}
      <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
        {/* Injury cards */}
        <div className="flex flex-col gap-4">
          {loading ? (
            <div className="glass-panel rounded-xl p-8 text-center text-text-muted">
              <Activity className="w-8 h-8 mx-auto mb-3 animate-pulse text-primary" />
              <div>Loading injury intelligence…</div>
            </div>
          ) : filtered.length === 0 ? (
            <div className="glass-panel rounded-xl p-8 text-center text-text-muted">
              <Shield className="w-8 h-8 mx-auto mb-3 opacity-30" />
              <div className="font-medium">No injuries above threshold</div>
              <div className="text-xs mt-1">Try lowering the impact threshold or check back later.</div>
            </div>
          ) : (
            filtered.map((player) => (
              <InjuryCard
                key={player.player_id}
                player={player}
                overrides={overrideMap}
                onOverride={handleOverride}
                pendingSet={pendingIds}
              />
            ))
          )}
        </div>

        {/* Sidebar: Override summary + live SSE indicator */}
        <div className="flex flex-col gap-4">
          <OverrideSummary overrides={activeOverrides} onRemove={handleRemoveOverride} />

          {/* SSE live feed */}
          <div className="glass-strip rounded-xl p-4 border border-white/10">
            <div className="flex items-center gap-2 mb-3">
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
              <span className="text-sm font-semibold text-text-primary">Live Feed</span>
              <span className="ml-auto text-xs text-text-muted">{ssePlayers.length} players</span>
            </div>
            <div className="flex flex-col gap-1.5 max-h-64 overflow-y-auto">
              {ssePlayers.slice(0, 20).map((p, i) => (
                <div key={i} className="flex items-center justify-between px-2 py-1 rounded bg-white/5 text-xs">
                  <span className="text-text-secondary truncate">{p.player_name}</span>
                  <span className={cn('font-medium ml-2 shrink-0', STATUS_COLOR[p.status] ?? 'text-text-muted')}>
                    {p.status}
                  </span>
                </div>
              ))}
              {ssePlayers.length === 0 && (
                <div className="text-xs text-text-muted text-center py-3">Waiting for updates…</div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
