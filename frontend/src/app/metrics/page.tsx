'use client'

import { useCallback, useEffect, useState, type CSSProperties } from 'react'
import { getAnalyticsRoi, getAnalyticsAccuracy, postContestResult } from '@/lib/api'

// ─── Types ─────────────────────────────────────────────────────────────────────

interface HealthData {
  status: string
  uptime_seconds?: number
  environment?: string
  workers?: number
  cache_size?: number
  cache_ttl?: number
  db_pool_in?: number
  db_pool_out?: number
}

interface RoiData {
  total_invested?: number
  total_won?: number
  profit?: number
  roi_percentage?: number
  total_contests?: number
  roi_by_type?: Record<string, { invested: number; won: number; roi: number; count?: number }>
  period_days?: number
}

interface AccuracyData {
  total_projections?: number
  mae?: number
  rmse?: number
  bias?: number
  period_days?: number | string
  by_day?: { date: string; count: number; mae: number; rmse: number; bias: number }[]
}

type Period = '3d' | '7d' | '14d' | '30d'

const PERIOD_DAYS: Record<Period, number> = { '3d': 3, '7d': 7, '14d': 14, '30d': 30 }

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'

// ─── Formatters ────────────────────────────────────────────────────────────────

function fmtUptime(secs?: number): string {
  if (!secs) return '—'
  const d = Math.floor(secs / 86400)
  const h = Math.floor((secs % 86400) / 3600)
  const m = Math.floor((secs % 3600) / 60)
  if (d > 0) return `${d}d ${h}h ${m}m`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function fmtMs(n?: number): string {
  if (n == null) return '—'
  return `${Math.round(n)}ms`
}

function fmtPct(n?: number): string {
  if (n == null) return '—'
  return `${(n * 100).toFixed(1)}%`
}

// ─── Stat card ─────────────────────────────────────────────────────────────────

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-surface-raised border border-surface-border rounded-xl px-5 py-4 flex-1 min-w-0">
      <div className="text-[10px] text-text-muted uppercase font-semibold tracking-[0.05em]">{label}</div>
      <div className="text-[26px] font-extrabold text-text-primary mt-1.5">{value}</div>
      {sub && <div className="text-[11px] text-text-muted mt-1">{sub}</div>}
    </div>
  )
}

// ─── Bar chart ─────────────────────────────────────────────────────────────────

function BarChart({ data }: { data: { date: string; completed: number; failed: number }[] }) {
  if (!data.length) return (
    <div className="flex items-center justify-center h-[140px] text-surface-border text-xs">
      No run data available
    </div>
  )

  const maxVal = Math.max(...data.map(d => d.completed + d.failed), 1)

  return (
    <div className="flex items-end gap-1 h-[140px] pt-3 pb-6">
      {data.map((d) => {
        const totalH = ((d.completed + d.failed) / maxVal) * 100
        const failRatio = (d.completed + d.failed) > 0 ? d.failed / (d.completed + d.failed) : 0
        const shortDate = d.date.slice(5)  // MM-DD
        return (
          <div key={d.date} className="flex-1 flex flex-col items-center gap-0.5">
            <div className="w-full flex flex-col justify-end h-[110px]">
              <div className="w-full flex flex-col h-[var(--total-h)] min-h-[2px]" style={{'--total-h': `${totalH}%`} as CSSProperties}>
                {d.failed > 0 && (
                  <div className="w-full rounded-t-sm bg-danger h-[var(--fail-h)]" style={{'--fail-h': `${failRatio * 100}%`} as CSSProperties} />
                )}
                <div className={`w-full flex-1 bg-primary${d.failed > 0 ? '' : ' rounded-t-sm'}`} />
              </div>
            </div>
            <span className="text-[8px] text-text-muted mt-0.5 -rotate-45 origin-top">{shortDate}</span>
          </div>
        )
      })}
    </div>
  )
}

// ─── Line chart (endpoint latency) ────────────────────────────────────────────

function LineChart({ data, label1 = 'MAE', label2 = 'RMSE' }: { data: { time: string; p50: number; p95: number }[]; label1?: string; label2?: string }) {
  if (!data.length) return (
    <div className="flex items-center justify-center h-[140px] text-surface-border text-xs">
      No trend data yet
    </div>
  )

  const W = 300, H = 110, PAD = { l: 28, r: 8, t: 8, b: 20 }
  const iW = W - PAD.l - PAD.r, iH = H - PAD.t - PAD.b
  const maxY = Math.max(...data.flatMap(d => [d.p50, d.p95]), 1)

  const px = (i: number) => PAD.l + (i / (data.length - 1)) * iW
  const py = (v: number) => PAD.t + iH - (v / maxY) * iH

  const path50 = data.map((d, i) => `${i === 0 ? 'M' : 'L'}${px(i)},${py(d.p50)}`).join(' ')
  const path95 = data.map((d, i) => `${i === 0 ? 'M' : 'L'}${px(i)},${py(d.p95)}`).join(' ')

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" className="overflow-visible">
      <path d={path50} fill="none" stroke="#3b82f6" strokeWidth={1.5} />
      <path d={path95} fill="none" stroke="#f59e0b" strokeWidth={1.5} strokeDasharray="4 2" />
      <text x={W - 2} y={py(data[data.length-1]?.p50 ?? 0)} fontSize={8} fill="#3b82f6" textAnchor="end">{label1}</text>
      <text x={W - 2} y={py(data[data.length-1]?.p95 ?? 0)} fontSize={8} fill="#f59e0b" textAnchor="end">{label2}</text>
    </svg>
  )
}

// ─── Info row ────────────────────────────────────────────────────────────────

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between py-1.5 border-b border-surface-border/40">
      <span className="text-xs text-text-muted">{label}</span>
      <span className="text-xs text-text-primary font-semibold">{value}</span>
    </div>
  )
}

// ─── Skeleton rows ────────────────────────────────────────────────────────────

function SkeletonRows({ count = 3 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="h-7 bg-surface-overlay rounded mb-1.5 opacity-40" />
      ))}
    </>
  )
}

// ─── Page ────────────────────────────────────────────────────────────────────

export default function MetricsPage() {
  const [period, setPeriod] = useState<Period>('7d')
  const [health, setHealth] = useState<HealthData | null>(null)
  const [roi, setRoi] = useState<RoiData | null>(null)
  const [accuracy, setAccuracy] = useState<AccuracyData | null>(null)
  const [loading, setLoading] = useState(false)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)

  // Contest log form
  const [showForm, setShowForm] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [formMsg, setFormMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [cf, setCf] = useState({
    contest_date: new Date().toISOString().slice(0, 10),
    contest_type: 'gpp' as 'gpp' | 'double_up' | 'cash' | 'winner_take_all',
    site: 'DK' as 'DK' | 'FD',
    entry_fee: '',
    payout: '',
    final_rank: '',
    total_entries: '',
    notes: '',
  })

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const [healthRes, roiRes, accRes] = await Promise.allSettled([
        fetch(API_BASE + '/health').then(r => r.json()),
        getAnalyticsRoi(PERIOD_DAYS[period]),
        getAnalyticsAccuracy(PERIOD_DAYS[period]),
      ])

      if (healthRes.status === 'fulfilled') setHealth(healthRes.value)
      if (roiRes.status === 'fulfilled' && roiRes.value.success) setRoi((roiRes.value.data ?? {}) as RoiData)
      if (accRes.status === 'fulfilled' && accRes.value.success) setAccuracy((accRes.value.data ?? {}) as AccuracyData)

      setLastRefresh(new Date())
    } finally {
      setLoading(false)
    }
  }, [period])

  useEffect(() => { fetchAll() }, [fetchAll])

  async function handleSubmitContest(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setFormMsg(null)
    const result = await postContestResult({
      contest_date: cf.contest_date,
      contest_type: cf.contest_type,
      site: cf.site,
      entry_fee: parseFloat(cf.entry_fee),
      payout: cf.payout ? parseFloat(cf.payout) : undefined,
      final_rank: cf.final_rank ? parseInt(cf.final_rank) : undefined,
      total_entries: cf.total_entries ? parseInt(cf.total_entries) : undefined,
      notes: cf.notes || undefined,
    })
    setSubmitting(false)
    if (result.success) {
      setFormMsg({ type: 'ok', text: 'Contest entry logged!' })
      setCf(c => ({ ...c, entry_fee: '', payout: '', final_rank: '', total_entries: '', notes: '' }))
      setShowForm(false)
      fetchAll()
    } else {
      setFormMsg({ type: 'err', text: result.error ?? 'Failed to log contest' })
    }
  }

  // Daily projections logged — bar chart data from by_day
  const dailyRuns: { date: string; completed: number; failed: number }[] = (
    accuracy?.by_day?.map(d => ({ date: d.date, completed: d.count, failed: 0 })) ??
    Array.from({ length: PERIOD_DAYS[period] }, (_, i) => {
      const d = new Date(); d.setDate(d.getDate() - (PERIOD_DAYS[period] - 1 - i))
      return { date: d.toISOString().slice(0, 10), completed: 0, failed: 0 }
    })
  )

  // MAE & RMSE trend for the accuracy line chart
  const accuracyTrend: { time: string; p50: number; p95: number }[] = (
    accuracy?.by_day?.map(d => ({ time: d.date.slice(5), p50: d.mae, p95: d.rmse })) ?? []
  )

  const PERIODS: Period[] = ['3d', '7d', '14d', '30d']

  return (
    <div className="bg-surface-base min-h-[calc(100vh-48px)] p-6">
      <div className="max-w-[1200px] mx-auto">

        {/* Header */}
        <div className="flex justify-between items-start mb-5">
          <div>
            <h1 className="m-0 text-[22px] font-extrabold text-text-primary">System Metrics</h1>
            <p className="m-0 mt-1 text-sm text-text-muted">
              Real-time monitoring — run rates, latency, errors
            </p>
          </div>
          <div className="flex items-center gap-2">
            {/* Period selector */}
            <div className="flex bg-surface-raised rounded-lg overflow-hidden">
              {PERIODS.map(p => (
                <button key={p} onClick={() => setPeriod(p)} className={`px-3 py-1.5 text-xs font-bold cursor-pointer border-none transition-colors ${period === p ? 'bg-primary text-white' : 'bg-transparent text-text-muted hover:text-text-secondary'}`}>{p}</button>
              ))}
            </div>
            <button
              onClick={() => { setShowForm(v => !v); setFormMsg(null) }}
              className="px-3.5 py-1.5 rounded-lg border-none bg-primary text-white text-xs font-semibold cursor-pointer hover:opacity-90 transition-opacity"
            >
              {showForm ? '✕ Close' : '+ Log Contest'}
            </button>
            <button onClick={fetchAll} disabled={loading} className="px-3.5 py-1.5 rounded-lg border-none bg-surface-raised text-text-secondary text-xs font-semibold cursor-pointer hover:bg-surface-overlay transition-colors disabled:cursor-not-allowed disabled:text-text-muted">
              {loading ? '⟳ Loading…' : '⟳ Refresh'}
            </button>
            {lastRefresh && (
              <span className="text-[11px] text-text-muted">
                Updated {lastRefresh.toLocaleTimeString()}
              </span>
            )}
          </div>
        </div>

        {/* Contest log form (collapsible) */}
        {showForm && (
          <div className="mb-5 bg-surface-raised border border-surface-border rounded-xl px-5 py-4">
            <div className="text-xs font-bold text-text-secondary uppercase tracking-[0.05em] mb-3">Log Contest Entry</div>
            <form onSubmit={handleSubmitContest}>
              <div className="grid grid-cols-4 gap-3 mb-3">
                <div className="flex flex-col gap-1">
                  <label className="text-[11px] text-text-muted">Date</label>
                  <input
                    type="date"
                    required
                    value={cf.contest_date}
                    onChange={e => setCf(c => ({ ...c, contest_date: e.target.value }))}
                    className="px-2 py-1.5 rounded-md border border-surface-border bg-surface-base text-text-primary text-xs outline-none focus:border-primary"
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-[11px] text-text-muted">Type</label>
                  <select
                    value={cf.contest_type}
                    onChange={e => setCf(c => ({ ...c, contest_type: e.target.value as typeof cf.contest_type }))}
                    className="px-2 py-1.5 rounded-md border border-surface-border bg-surface-base text-text-primary text-xs outline-none focus:border-primary"
                  >
                    <option value="gpp">GPP</option>
                    <option value="cash">Cash</option>
                    <option value="double_up">Double-Up</option>
                    <option value="winner_take_all">Winner Take All</option>
                  </select>
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-[11px] text-text-muted">Site</label>
                  <select
                    value={cf.site}
                    onChange={e => setCf(c => ({ ...c, site: e.target.value as 'DK' | 'FD' }))}
                    className="px-2 py-1.5 rounded-md border border-surface-border bg-surface-base text-text-primary text-xs outline-none focus:border-primary"
                  >
                    <option value="DK">DraftKings</option>
                    <option value="FD">FanDuel</option>
                  </select>
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-[11px] text-text-muted">Entry Fee ($)</label>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    required
                    placeholder="e.g. 25"
                    value={cf.entry_fee}
                    onChange={e => setCf(c => ({ ...c, entry_fee: e.target.value }))}
                    className="px-2 py-1.5 rounded-md border border-surface-border bg-surface-base text-text-primary text-xs outline-none focus:border-primary"
                  />
                </div>
              </div>
              <div className="grid grid-cols-4 gap-3 mb-3">
                <div className="flex flex-col gap-1">
                  <label className="text-[11px] text-text-muted">Payout ($)</label>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    placeholder="0.00"
                    value={cf.payout}
                    onChange={e => setCf(c => ({ ...c, payout: e.target.value }))}
                    className="px-2 py-1.5 rounded-md border border-surface-border bg-surface-base text-text-primary text-xs outline-none focus:border-primary"
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-[11px] text-text-muted">Final Rank</label>
                  <input
                    type="number"
                    min="1"
                    step="1"
                    placeholder="optional"
                    value={cf.final_rank}
                    onChange={e => setCf(c => ({ ...c, final_rank: e.target.value }))}
                    className="px-2 py-1.5 rounded-md border border-surface-border bg-surface-base text-text-primary text-xs outline-none focus:border-primary"
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-[11px] text-text-muted">Total Entries</label>
                  <input
                    type="number"
                    min="1"
                    step="1"
                    placeholder="optional"
                    value={cf.total_entries}
                    onChange={e => setCf(c => ({ ...c, total_entries: e.target.value }))}
                    className="px-2 py-1.5 rounded-md border border-surface-border bg-surface-base text-text-primary text-xs outline-none focus:border-primary"
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-[11px] text-text-muted">Notes</label>
                  <input
                    type="text"
                    placeholder="optional"
                    value={cf.notes}
                    onChange={e => setCf(c => ({ ...c, notes: e.target.value }))}
                    className="px-2 py-1.5 rounded-md border border-surface-border bg-surface-base text-text-primary text-xs outline-none focus:border-primary"
                  />
                </div>
              </div>
              <div className="flex items-center gap-3">
                <button
                  type="submit"
                  disabled={submitting || !cf.entry_fee}
                  className="px-4 py-1.5 rounded-lg border-none bg-primary text-white text-xs font-semibold cursor-pointer hover:opacity-90 transition-opacity disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {submitting ? 'Saving…' : 'Save Entry'}
                </button>
                {formMsg && (
                  <span className={`text-xs font-semibold ${formMsg.type === 'ok' ? 'text-success' : 'text-danger'}`}>
                    {formMsg.text}
                  </span>
                )}
              </div>
            </form>
          </div>
        )}

        {/* Summary stat cards */}
        <div className="flex gap-3 mb-5">
          <StatCard label="Uptime"       value={fmtUptime(health?.uptime_seconds)} />
          <StatCard label="Avg MAE"      value={accuracy?.mae != null ? accuracy.mae.toFixed(3) : '—'} sub="mean abs error" />
          <StatCard label="Projections"  value={(accuracy?.total_projections ?? 0).toString()} sub="logged this period" />
          <StatCard label="RMSE"         value={accuracy?.rmse != null ? accuracy.rmse.toFixed(3) : '—'} />
          <StatCard label="Proj Bias"    value={accuracy?.bias != null ? accuracy.bias.toFixed(3) : '—'} />
        </div>

        {/* Charts row */}
        <div className="flex gap-4 mb-5">
          <div className="flex-1 bg-surface-raised border border-surface-border rounded-xl px-5 py-4">
            <div className="text-xs font-bold text-text-secondary uppercase tracking-[0.05em] mb-2">Daily Runs</div>
            <div className="flex gap-3 mb-2">
              <div className="flex items-center gap-1.5">
                <div className="w-2.5 h-2.5 bg-primary rounded-sm" />
                <span className="text-[11px] text-text-muted">Completed</span>
              </div>
              <div className="flex items-center gap-1.5">
                <div className="w-2.5 h-2.5 bg-danger rounded-sm" />
                <span className="text-[11px] text-text-muted">Failed</span>
              </div>
            </div>
            <BarChart data={dailyRuns} />
          </div>

          <div className="flex-1 bg-surface-raised border border-surface-border rounded-xl px-5 py-4">
            <div className="text-xs font-bold text-text-secondary uppercase tracking-[0.05em] mb-2">Accuracy Trend</div>
            <div className="flex gap-3 mb-2">
              <div className="flex items-center gap-1.5">
                <div className="w-4 h-0.5 bg-primary" />
                <span className="text-[11px] text-text-muted">MAE</span>
              </div>
              <div className="flex items-center gap-1.5">
                <div className="w-4 h-0.5 bg-warning border-t border-dashed border-warning" />
                <span className="text-[11px] text-text-muted">RMSE</span>
              </div>
            </div>
            <LineChart data={accuracyTrend} label1="MAE" label2="RMSE" />
          </div>
        </div>

        {/* Lower panels: System / Workers / Run Stats */}
        <div className="flex gap-4">

          {/* System info */}
          <div className="flex-1 bg-surface-raised border border-surface-border rounded-xl px-5 py-4">
            <div className="text-xs font-bold text-text-secondary uppercase tracking-[0.05em] mb-3">System</div>
            <InfoRow label="Environment"  value={health?.environment ?? '—'} />
            <InfoRow label="Uptime"       value={fmtUptime(health?.uptime_seconds)} />
            <InfoRow label="Workers"      value={String(health?.workers ?? '—')} />
            <InfoRow label="Cache Size"   value={health?.cache_size != null ? String(health.cache_size) : '—'} />
            <InfoRow label="Cache TTL"    value={health?.cache_ttl != null ? `${health.cache_ttl}s` : '—'} />
            <InfoRow label="DB Pool In"   value={health?.db_pool_in != null ? String(health.db_pool_in) : '—'} />
            <InfoRow label="DB Pool Out"  value={health?.db_pool_out != null ? String(health.db_pool_out) : '—'} />
          </div>

          {/* Workers panel */}
          <div className="flex-1 bg-surface-raised border border-surface-border rounded-xl px-5 py-4">
            <div className="text-xs font-bold text-text-secondary uppercase tracking-[0.05em] mb-3">Workers</div>
            {loading ? <SkeletonRows count={4} /> : (
              <div className="text-surface-border text-xs py-3">No active workers</div>
            )}
          </div>

          {/* Run Stats panel */}
          <div className="flex-1 bg-surface-raised border border-surface-border rounded-xl px-5 py-4">
            <div className="text-xs font-bold text-text-secondary uppercase tracking-[0.05em] mb-3">
              Run Stats {period}
            </div>
            {loading ? <SkeletonRows count={5} /> : accuracy ? (
              <>
                <InfoRow label="Projections"   value={String(accuracy.total_projections ?? 0)} />
                <InfoRow label="MAE"           value={accuracy.mae != null ? accuracy.mae.toFixed(3) : '—'} />
                <InfoRow label="RMSE"          value={accuracy.rmse != null ? accuracy.rmse.toFixed(3) : '—'} />
                <InfoRow label="Proj Bias"     value={accuracy.bias != null ? accuracy.bias.toFixed(3) : '—'} />
                <InfoRow label="Period"        value={String(accuracy.period_days ?? '—') + (typeof accuracy.period_days === 'number' ? 'd' : '')} />
              </>
            ) : (
              <div className="text-surface-border text-xs py-3">No reconciled projections yet</div>
            )}

            {/* ROI section */}
            {roi && (
              <>
                <div className="mt-4 mb-2 text-[11px] text-text-muted uppercase tracking-[0.05em]">ROI</div>
                <InfoRow label="Total Contests" value={String(roi.total_contests ?? 0)} />
                <InfoRow label="Total Invested" value={roi.total_invested != null ? `$${roi.total_invested.toFixed(2)}` : '—'} />
                <InfoRow label="Total Won"      value={roi.total_won != null ? `$${roi.total_won.toFixed(2)}` : '—'} />
                <InfoRow label="ROI %"          value={roi.roi_percentage != null ? `${roi.roi_percentage.toFixed(1)}%` : '—'} />
                {roi.roi_by_type && Object.keys(roi.roi_by_type).length > 0 && (
                  <>
                    <div className="mt-3 mb-1.5 text-[11px] text-text-muted uppercase tracking-[0.05em]">By Type</div>
                    {Object.entries(roi.roi_by_type).map(([type, stats]) => (
                      <div key={type} className="flex justify-between items-center py-0.5">
                        <span className="text-xs text-text-muted capitalize">{type.replace('_', ' ')}</span>
                        <span className={`text-xs font-semibold tabular-nums ${stats.roi >= 0 ? 'text-success' : 'text-danger'}`}>
                          {stats.roi >= 0 ? '+' : ''}{stats.roi.toFixed(1)}%
                          <span className="ml-1 font-normal text-text-muted">({stats.count ?? 0})</span>
                        </span>
                      </div>
                    ))}
                  </>
                )}
              </>
            )}
          </div>

        </div>
      </div>
    </div>
  )
}
