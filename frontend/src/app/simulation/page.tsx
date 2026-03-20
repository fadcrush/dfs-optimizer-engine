'use client'

import { useRef, useState, useEffect } from 'react'
import { runFDOptimizer } from '@/lib/api'
import { useLatestSlate } from '@/hooks/useLatestSlate'
import { SlateSelector } from '@/components/shared/SlateSelector'
import { useInjuryEvents } from '@/hooks/useInjuryEvents'
import { cn } from '@/lib/utils'

// ─── Types ───────────────────────────────────────────────────────────────────

interface SimLineup {
  lineup_id: number
  projected_pts: number
  salary: number
  exposure: Record<string, number>
  players: string[]
}

interface SimResult {
  sims: number
  lineups: SimLineup[]
  avg_projected: number
  top_players: { name: string; exposure: number }[]
}

// ─── Config ───────────────────────────────────────────────────────────────────

const SIM_OPTIONS = [
  { label: '1,000', value: 1000 },
  { label: '5,000', value: 5000 },
  { label: '10,000', value: 10000 },
  { label: '25,000', value: 25000 },
]

const CONTEST_TYPES = ['LARGE GPP', 'SMALL GPP', 'CASH', 'SHOWDOWN']
const SPORT_OPTIONS = ['NBA', 'NFL'] as const
const SITE_OPTIONS = ['DK', 'FD'] as const

// ─── Helper ───────────────────────────────────────────────────────────────────

function cleanName(raw: string): string {
  if (!raw) return ''
  const m = raw.match(/^\d+:(.+)$/)
  return m ? m[1] : raw
}

function formatSalary(n: number): string {
  return n >= 1000 ? `$${(n / 1000).toFixed(1)}K` : `$${n}`
}

// ─── Monte Carlo helpers ───────────────────────────────────────────────────────

/** Box-Muller transform — returns one N(0,1) sample */
function gaussian(): number {
  let u = 0, v = 0
  while (u === 0) u = Math.random()
  while (v === 0) v = Math.random()
  return Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v)
}

function percentile(sorted: number[], p: number): number {
  const idx = Math.floor(sorted.length * p / 100)
  return sorted[Math.min(idx, sorted.length - 1)]
}

// ─── Score Distribution Chart ──────────────────────────────────────────────────

function ScoreDistribution({ lineups, nSims }: { lineups: SimLineup[]; nSims: number }) {
  if (!lineups.length) return null

  // Simulate: each lineup draws (nSims / lineups.length) outcomes
  const DRAWS = Math.max(200, Math.min(2000, Math.floor(nSims / lineups.length)))
  const SD_FACTOR = 0.22
  const scores: number[] = []

  for (const lu of lineups) {
    const sd = lu.projected_pts * SD_FACTOR
    for (let k = 0; k < DRAWS; k++) {
      scores.push(lu.projected_pts + gaussian() * sd)
    }
  }
  scores.sort((a, b) => a - b)

  const p50 = percentile(scores, 50)
  const p75 = percentile(scores, 75)
  const p90 = percentile(scores, 90)

  // Build histogram
  const BINS = 28
  const lo = scores[0] * 0.96
  const hi = scores[scores.length - 1] * 1.02
  const step = (hi - lo) / BINS
  const counts = Array(BINS).fill(0)
  for (const s of scores) {
    const b = Math.min(BINS - 1, Math.floor((s - lo) / step))
    counts[b]++
  }
  const maxCount = Math.max(...counts)

  const W = 580
  const H = 130
  const PAD = { top: 10, right: 12, bottom: 26, left: 8 }
  const innerW = W - PAD.left - PAD.right
  const innerH = H - PAD.top - PAD.bottom
  const barW = innerW / BINS

  const xPos = (score: number) => PAD.left + ((score - lo) / (hi - lo)) * innerW

  return (
    <div className="bg-surface-base border border-surface-border rounded-lg px-3.5 py-3">
      <div className="text-[11px] text-text-muted font-bold uppercase tracking-wide mb-2.5">
        Score Distribution ({scores.length.toLocaleString()} simulated outcomes)
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="w-full block">
        {/* Bars */}
        {counts.map((c, i) => {
          const bh = maxCount > 0 ? (c / maxCount) * innerH : 0
          const hi50 = (lo + i * step) >= p50
          const hi75 = (lo + i * step) >= p75
          const hi90 = (lo + i * step) >= p90
          const fill = hi90 ? '#f59e0b' : hi75 ? '#22d3ee' : hi50 ? '#3b82f6' : '#1e40af'
          return (
            <rect
              key={i}
              x={PAD.left + i * barW + 1}
              y={PAD.top + innerH - bh}
              width={Math.max(1, barW - 2)}
              height={bh}
              fill={fill}
              opacity={0.85}
            />
          )
        })}
        {/* Percentile lines */}
        {[
          { v: p50, label: 'p50', color: '#3b82f6' },
          { v: p75, label: 'p75', color: '#22d3ee' },
          { v: p90, label: 'p90', color: '#f59e0b' },
        ].map(({ v, label, color }) => {
          const x = xPos(v)
          return (
            <g key={label}>
              <line x1={x} y1={PAD.top} x2={x} y2={PAD.top + innerH} stroke={color} strokeWidth={1.5} strokeDasharray="3 2" />
              <text x={x + 3} y={PAD.top + 9} fill={color} fontSize={9} fontWeight="700">{label}</text>
              <text x={x + 3} y={PAD.top + 19} fill={color} fontSize={8} opacity={0.8}>{v.toFixed(1)}</text>
            </g>
          )
        })}
        {/* X axis labels */}
        {[0, 0.25, 0.5, 0.75, 1].map(t => {
          const val = lo + t * (hi - lo)
          return (
            <text key={t} x={PAD.left + t * innerW} y={H - 4} fill="#475569" fontSize={8} textAnchor="middle">
              {val.toFixed(0)}
            </text>
          )
        })}
      </svg>

      {/* Legend */}
      <div className="flex gap-4 mt-1.5">
        {[
          { color: '#1e40af', label: 'Below p50' },
          { color: '#3b82f6', label: 'p50–p75' },
          { color: '#22d3ee', label: 'p75–p90' },
          { color: '#f59e0b', label: 'Top 10%' },
        ].map(({ color, label }) => (
          <div key={label} className="flex items-center gap-1.5 text-[10px] text-text-muted">
            <div className="w-2.5 h-2.5 rounded-sm" style={{ background: color }} />
            {label}
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Left panel config ────────────────────────────────────────────────────────

interface ConfigPanelProps {
  slate: File | null
  onSelectSlate: () => void
  fileRef: React.RefObject<HTMLInputElement>
  onFileChange: (e: React.ChangeEvent<HTMLInputElement>) => void
  sport: 'NBA' | 'NFL'
  setSport: (s: 'NBA' | 'NFL') => void
  site: 'DK' | 'FD'
  setSite: (s: 'DK' | 'FD') => void
  nSims: number
  setNSims: (n: number) => void
  nLineups: number
  setNLineups: (n: number) => void
  contestType: string
  setContestType: (c: string) => void
  onRun: () => void
  loading: boolean
}

// ─── Style constants ──────────────────────────────────────────────────────────

const lblCls = 'block text-[11px] text-text-muted uppercase mb-1 font-semibold tracking-wide'
const inpCls = 'w-full mt-1.5 bg-surface-base text-text-primary border border-surface-border rounded px-2.5 py-2 text-sm outline-none focus:border-primary box-border'

function ConfigPanel(p: ConfigPanelProps) {
  return (
    <div className="w-[280px] shrink-0 bg-surface-overlay border border-surface-border rounded-xl p-5 flex flex-col gap-[18px]">
      <div className="text-sm font-bold text-text-primary mb-0.5">Simulation Config</div>

      {/* Sport / Site toggles */}
      <div>
        <label className={lblCls}>Sport</label>
        <div className="flex gap-1.5 mt-1.5">
          {SPORT_OPTIONS.map(s => (
            <button
              key={s}
              onClick={() => p.setSport(s)}
              className={cn('px-3 py-1 rounded text-xs font-semibold cursor-pointer border-none transition-colors',
                p.sport === s ? 'bg-primary text-white' : 'bg-surface-border text-text-muted hover:bg-surface-overlay'
              )}
            >{s}</button>
          ))}
        </div>
      </div>
      <div>
        <label className={lblCls}>Platform</label>
        <div className="flex gap-1.5 mt-1.5">
          {SITE_OPTIONS.map(s => (
            <button
              key={s}
              onClick={() => p.setSite(s)}
              className={cn('px-3 py-1 rounded text-xs font-semibold cursor-pointer border-none transition-colors',
                p.site === s ? 'bg-primary text-white' : 'bg-surface-border text-text-muted hover:bg-surface-overlay'
              )}
            >
              {s === 'DK' ? 'DraftKings' : 'FanDuel'}
            </button>
          ))}
        </div>
      </div>

      {/* Slate */}
      <div>
        <label className={lblCls}>Slate CSV</label>
        <div
          onClick={p.onSelectSlate}
          className={cn(
            'mt-1.5 px-3 py-2 bg-surface-base border border-surface-border rounded text-xs cursor-pointer overflow-hidden text-ellipsis whitespace-nowrap transition-colors hover:border-primary/60',
            p.slate ? 'text-text-primary' : 'text-text-muted'
          )}
        >
          {p.slate ? p.slate.name : 'Click to select…'}
        </div>
        <input ref={p.fileRef} type="file" accept=".csv" onChange={p.onFileChange} className="hidden" />
      </div>

      {/* Simulations */}
      <div>
        <label className={lblCls}>Simulations</label>
        <div className="flex gap-1.5 mt-1.5 flex-wrap">
          {SIM_OPTIONS.map(({ label, value }) => (
            <button
              key={value}
              onClick={() => p.setNSims(value)}
              className={cn('px-3 py-1 rounded text-xs font-semibold cursor-pointer border-none transition-colors',
                p.nSims === value ? 'bg-primary text-white' : 'bg-surface-border text-text-muted hover:bg-surface-overlay'
              )}
            >{label}</button>
          ))}
        </div>
      </div>

      {/* Lineups to simulate */}
      <div>
        <label className={lblCls}>Lineups to Simulate</label>
        <input
          type="number" min={1} max={500} value={p.nLineups}
          onChange={e => p.setNLineups(Math.max(1, Math.min(500, +e.target.value)))}
          className={inpCls}
        />
      </div>

      {/* Contest type */}
      <div>
        <label className={lblCls}>Contest Type</label>
        <select value={p.contestType} onChange={e => p.setContestType(e.target.value)} className={inpCls}>
          {CONTEST_TYPES.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>

      {/* Run button */}
      <button
        onClick={p.onRun}
        disabled={p.loading || !p.slate}
        className="mt-1 py-2.5 rounded-lg border-none bg-primary text-white font-bold text-sm cursor-pointer hover:bg-primary-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {p.loading ? 'Running…' : 'Run Simulation'}
      </button>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-3">
      <div className="text-[42px] opacity-25">⇌</div>
      <div className="text-text-muted text-sm font-semibold">Configure and run a simulation</div>
      <div className="text-surface-border text-xs">Upload a slate CSV and click Run Simulation</div>
    </div>
  )
}

// ─── Results panel ─────────────────────────────────────────────────────────────

function ResultsPanel({ result }: { result: SimResult }) {
  return (
    <div className="flex flex-col gap-4 h-full">

      {/* Summary stat cards */}
      <div className="flex gap-3">
        {[
          { label: 'Simulations', value: result.sims.toLocaleString() },
          { label: 'Lineups', value: result.lineups.length },
          { label: 'Avg. Projected', value: result.avg_projected.toFixed(1) + ' pts' },
        ].map(c => (
          <div key={c.label} className="flex-1 bg-surface-base border border-surface-border rounded-lg px-4 py-3">
            <div className="text-[10px] text-text-muted uppercase font-semibold">{c.label}</div>
            <div className="text-[22px] font-extrabold text-text-primary mt-1">{c.value}</div>
          </div>
        ))}
      </div>

      {/* Monte Carlo score distribution */}
      <ScoreDistribution lineups={result.lineups} nSims={result.sims} />

      <div className="bg-surface-base border border-surface-border rounded-lg overflow-hidden">
        <div className="px-3.5 py-2.5 border-b border-surface-border text-xs font-bold text-text-secondary uppercase tracking-wide">
          Top Player Exposure
        </div>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                {['Player', 'Exposure'].map(h => (
                  <th key={h} className="px-3.5 py-1.5 text-[10px] text-text-muted text-left font-semibold uppercase border-b border-surface-border">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.top_players.map((p, i) => (
                <tr key={p.name} className={i % 2 === 0 ? '' : 'bg-surface-raised'}>
                  <td className="px-3.5 py-2 text-sm text-text-primary font-semibold">{p.name}</td>
                  <td className="px-3.5 py-2">
                    <div className="flex items-center gap-2">
                      <div className="flex-1 bg-surface-border rounded h-1.5">
                        <div
                          className={cn('h-full rounded', p.exposure >= 40 ? 'bg-warning' : 'bg-primary')}
                          style={{ width: `${p.exposure}%` }}
                        />
                      </div>
                      <span className="text-xs text-text-secondary w-9 text-right">{p.exposure.toFixed(0)}%</span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Lineup list */}
      <div className="bg-surface-base border border-surface-border rounded-lg overflow-hidden flex-1">
        <div className="px-3.5 py-2.5 border-b border-surface-border text-xs font-bold text-text-secondary uppercase tracking-wide">
          Simulated Lineups
        </div>
        <div className="overflow-y-auto max-h-80">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                {['#', 'Players', 'Salary', 'Proj'].map(h => (
                  <th key={h} className="px-3 py-1.5 text-[10px] text-text-muted text-left font-semibold uppercase border-b border-surface-border sticky top-0 bg-surface-base">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.lineups.map((lu, i) => (
                <tr key={lu.lineup_id} className={i % 2 === 0 ? '' : 'bg-surface-raised/30'}>
                  <td className="px-3 py-2 text-xs text-text-muted w-8">{i + 1}</td>
                  <td className="px-3 py-2 text-xs text-text-secondary">
                    {lu.players.map(cleanName).join(', ')}
                  </td>
                  <td className="px-3 py-2 text-xs text-text-muted whitespace-nowrap">{formatSalary(lu.salary)}</td>
                  <td className="px-3 py-2 text-sm font-bold text-[#22d3ee] whitespace-nowrap">{lu.projected_pts.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function SimulationPage() {
  const fileRef = useRef<HTMLInputElement>(null)
  const [slate, setSlate] = useState<File | null>(null)
  const [sport, setSport] = useState<'NBA' | 'NFL'>('NBA')
  const [site, setSite] = useState<'DK' | 'FD'>('DK')
  const [nSims, setNSims] = useState(10000)
  const [nLineups, setNLineups] = useState(150)
  const [contestType, setContestType] = useState('LARGE GPP')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [planGated, setPlanGated] = useState(false)
  const [result, setResult] = useState<SimResult | null>(null)

  // Slate auto-load
  const { slates, selectedSlate, setSelectedId, slateFile, loading: slateLoading } = useLatestSlate()
  const { players: injuredPlayers } = useInjuryEvents()
  const autoRanRef = useRef<string | null>(null)
  useEffect(() => {
    if (!slateFile) return
    setSlate(slateFile)
    if (autoRanRef.current === slateFile.name) return
    autoRanRef.current = slateFile.name
    setLoading(true)
    setError(null)
    setPlanGated(false)
    runFDOptimizer(slateFile, {
      site,
      numLineups: nLineups,
      minSalary: site === 'DK' ? 47000 : 57000,
      maxSalary: site === 'DK' ? 50000 : 60000,
      maxExposure: 0.6,
    }).then(res => {
      setLoading(false)
      if (!res.success || !res.data) {
        if (res.planGated) { setPlanGated(true); return }
        setError(res.error ?? 'Simulation failed'); return
      }
      const PLAYER_SLOTS = ['PG', 'PG_2', 'SG', 'SG_2', 'SF', 'SF_2', 'PF', 'PF_2', 'C', 'G', 'F', 'UTIL']
      const lineups: SimLineup[] = (res.data.lineups ?? []).map((lu, i) => {
        const players: string[] = PLAYER_SLOTS
          .map(slot => lu[slot])
          .filter((v): v is string => typeof v === 'string' && v.length > 0)
        return { lineup_id: lu.lineup_num ?? i + 1, projected_pts: lu.projected_points ?? 0, salary: lu.total_salary ?? 0, exposure: {}, players }
      })
      const expMap: Record<string, number> = {}
      lineups.forEach(lu => lu.players.forEach(p => { expMap[p] = (expMap[p] ?? 0) + 1 }))
      const top_players = Object.entries(expMap)
        .map(([name, count]) => ({ name: cleanName(name), exposure: (count / lineups.length) * 100 }))
        .sort((a, b) => b.exposure - a.exposure)
        .slice(0, 10)
      const avg_projected = lineups.length
        ? lineups.reduce((s, l) => s + l.projected_pts, 0) / lineups.length
        : 0
      setResult({ sims: nSims, lineups, avg_projected, top_players })
    })
  }, [slateFile]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSlate(e.target.files?.[0] ?? null)
    setResult(null)
    setError(null)
  }

  const handleRun = async () => {
    if (!slate) return
    setLoading(true)
    setError(null)
    setPlanGated(false)

    try {
      const res = await runFDOptimizer(slate, {
        site,
        numLineups: nLineups,
        minSalary: site === 'DK' ? 47000 : 57000,
        maxSalary: site === 'DK' ? 50000 : 60000,
        maxExposure: 0.6,
      })

      if (!res.success || !res.data) {
        if (res.planGated) { setPlanGated(true); return }
        setError(res.error ?? 'Simulation failed')
        return
      }

      // Build SimResult from optimizer response
      const PLAYER_SLOTS = ['PG', 'PG_2', 'SG', 'SG_2', 'SF', 'SF_2', 'PF', 'PF_2', 'C', 'G', 'F', 'UTIL']
      const lineups: SimLineup[] = (res.data.lineups ?? []).map((lu, i) => {
        const players: string[] = PLAYER_SLOTS
          .map(slot => lu[slot])
          .filter((v): v is string => typeof v === 'string' && v.length > 0)
        return {
          lineup_id: lu.lineup_num ?? i + 1,
          projected_pts: lu.projected_points ?? 0,
          salary: lu.total_salary ?? 0,
          exposure: {},
          players,
        }
      })

      // Count exposure
      const expMap: Record<string, number> = {}
      lineups.forEach(lu => lu.players.forEach(p => { expMap[p] = (expMap[p] ?? 0) + 1 }))
      const top_players = Object.entries(expMap)
        .map(([name, count]) => ({ name: cleanName(name), exposure: (count / lineups.length) * 100 }))
        .sort((a, b) => b.exposure - a.exposure)
        .slice(0, 10)

      const avg_projected = lineups.length ? lineups.reduce((s, l) => s + l.projected_pts, 0) / lineups.length : 0

      setResult({ sims: nSims, lineups, avg_projected, top_players })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="bg-surface-base min-h-[calc(100vh-48px)] p-6">
      <div className="max-w-[1200px] mx-auto">

        {/* Page header */}
        <div className="mb-5">
          <h1 className="m-0 text-[22px] font-extrabold text-text-primary">Simulation</h1>
          <p className="mt-1 mb-0 text-sm text-text-muted">
            Monte Carlo lineup simulation — tournament confidence and exposure analysis
          </p>
        </div>

        {planGated && (
          <div className="bg-[#451a03] border border-[#f59e0b]/50 rounded-lg px-4 py-3 mb-4 flex items-start gap-3">
            <span className="text-[#fbbf24] text-base mt-0.5">&#9888;</span>
            <div>
              <p className="m-0 text-sm font-bold text-[#fde68a]">Pro plan required</p>
              <p className="m-0 mt-1 text-xs text-[#fde68a]/80">The Simulation engine is a Pro feature. Upgrade to unlock Monte Carlo lineup simulation.</p>
              <a href="/billing" className="inline-block mt-2 px-3 py-1.5 rounded text-xs font-bold bg-[#f59e0b] text-[#1c1917] no-underline hover:bg-[#fbbf24] transition-colors">
                Upgrade to Pro &rarr;
              </a>
            </div>
          </div>
        )}
        {error && !planGated && (
          <div className="bg-danger-muted border border-danger/40 rounded-lg px-4 py-2.5 mb-4 text-danger text-sm">
            {error}
          </div>
        )}
        {/* Injury warning */}
        {(() => {
          const out = injuredPlayers.filter(p => p.status.toUpperCase() === 'OUT')
          const gtd = injuredPlayers.filter(p => ['GTD', 'QUESTIONABLE', 'DOUBTFUL'].includes(p.status.toUpperCase()))
          if (!out.length && !gtd.length) return null
          return (
            <div className="bg-warning-muted border border-warning/30 rounded-lg px-4 py-2.5 mb-4 text-xs">
              <span className="text-warning font-bold mr-2">⚠ Injury Alert</span>
              {out.length > 0 && (
                <span className="text-danger mr-3">
                  <strong>OUT:</strong> {out.map(p => p.player_name).join(', ')}
                </span>
              )}
              {gtd.length > 0 && (
                <span className="text-warning">
                  <strong>GTD/Q:</strong> {gtd.map(p => p.player_name).join(', ')}
                </span>
              )}
              <span className="text-text-muted ml-2">Verify lineup before locking.</span>
            </div>
          )
        })()}

        {/* Slate selector */}
        <SlateSelector
          slates={slates}
          selected={selectedSlate}
          loading={slateLoading}
          onSelect={setSelectedId}
          onFileOverride={f => { setSlate(f); setResult(null) }}
        />

        {/* Two-panel layout */}
        <div className="flex gap-4 items-start">
          <ConfigPanel
            slate={slate}
            onSelectSlate={() => fileRef.current?.click()}
            fileRef={fileRef}
            onFileChange={handleFileChange}
            sport={sport}
            setSport={setSport}
            site={site}
            setSite={setSite}
            nSims={nSims}
            setNSims={setNSims}
            nLineups={nLineups}
            setNLineups={setNLineups}
            contestType={contestType}
            setContestType={setContestType}
            onRun={handleRun}
            loading={loading}
          />

          {/* Right panel */}
          <div className="flex-1 min-w-0 bg-surface-overlay border border-surface-border rounded-xl p-5 min-h-[480px]">
            {loading ? (
              <div className="flex items-center justify-center h-full text-text-muted text-sm">
                Running {nSims.toLocaleString()} simulations…
              </div>
            ) : result ? (
              <ResultsPanel result={result} />
            ) : (
              <EmptyState />
            )}
          </div>
        </div>

      </div>
    </div>
  )
}
