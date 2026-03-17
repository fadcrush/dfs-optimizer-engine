'use client'

import { useRef, useState, useEffect } from 'react'
import { runProjections, type PlayerProjection } from '@/lib/api'
import { formatSalary } from '@/lib/utils'
import { useLatestSlate } from '@/hooks/useLatestSlate'
import { SlateSelector } from '@/components/shared/SlateSelector'
import { useInjuryEvents } from '@/hooks/useInjuryEvents'

// ─── Types ───────────────────────────────────────────────────────────────────

type Category = 'Value' | 'Leverage' | 'Chalk' | 'Pivot'

interface EVPlayer extends PlayerProjection {
  ev_score: number
  category: Category
}

type FilterTab = 'All' | Category

// ─── EV computation ──────────────────────────────────────────────────────────

function computeEV(players: PlayerProjection[]): EVPlayer[] {
  if (!players.length) return []

  return players.map((p) => {
    const valueBase = p.salary > 0 ? p.projection / (p.salary / 1000) : 0
    const ownFactor = Math.max(0.35, 1 - p.ownership / 150)
    const ev_score = parseFloat((valueBase * ownFactor * 1.55).toFixed(1))

    let category: Category
    if (p.ownership >= 25) {
      category = 'Chalk'
    } else if (p.ownership < 10) {
      category = valueBase >= 4.5 ? 'Pivot' : 'Leverage'
    } else if (ev_score >= 7.5 || (ev_score >= 6.5 && p.ownership < 18)) {
      category = 'Value'
    } else {
      category = 'Leverage'
    }

    return { ...p, ev_score, category }
  })
}

// ─── Category styling ────────────────────────────────────────────────────────

const CAT_COLOR: Record<Category, string> = {
  Value:    '#4ade80',
  Leverage: '#60a5fa',
  Chalk:    '#fbbf24',
  Pivot:    '#c084fc',
}

const CAT_CLS: Record<Category, string> = {
  Value:    'bg-success-muted text-success',
  Leverage: 'bg-primary-muted text-primary',
  Chalk:    'bg-warning-muted text-warning',
  Pivot:    'bg-[#3b0764] text-[#c084fc]',
}

function CategoryBadge({ cat }: { cat: Category }) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-[11px] font-bold ${CAT_CLS[cat]}`}>
      {cat}
    </span>
  )
}

// ─── Bubble chart (pure SVG) ─────────────────────────────────────────────────

function BubbleChart({ players }: { players: EVPlayer[] }) {
  if (!players.length) return (
    <div className="flex items-center justify-center h-full text-text-muted text-sm">
      Run projections to see chart
    </div>
  )

  const W = 400, H = 260, PAD = { l: 40, r: 16, t: 16, b: 36 }
  const innerW = W - PAD.l - PAD.r
  const innerH = H - PAD.t - PAD.b

  const maxOwn = Math.max(...players.map(p => p.ownership), 1)
  const maxProj = Math.max(...players.map(p => p.projection), 1)
  const maxEV = Math.max(...players.map(p => p.ev_score), 1)

  const cx = (own: number) => PAD.l + (own / (maxOwn * 1.1)) * innerW
  const cy = (proj: number) => PAD.t + innerH - (proj / (maxProj * 1.1)) * innerH
  const cr = (ev: number) => 4 + (ev / maxEV) * 8

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ overflow: 'visible' }}>
      {/* Axes */}
      <line x1={PAD.l} y1={PAD.t} x2={PAD.l} y2={PAD.t + innerH} stroke="#1e293b" strokeWidth={1} />
      <line x1={PAD.l} y1={PAD.t + innerH} x2={PAD.l + innerW} y2={PAD.t + innerH} stroke="#1e293b" strokeWidth={1} />

      {/* Axis labels */}
      <text x={PAD.l + innerW / 2} y={H - 4} textAnchor="middle" fontSize={9} fill="#64748b">Own%</text>
      <text x={10} y={PAD.t + innerH / 2} textAnchor="middle" fontSize={9} fill="#64748b" transform={`rotate(-90, 10, ${PAD.t + innerH / 2})`}>Proj</text>

      {/* Bubbles */}
      {players.map((p) => {
        const col = CAT_COLOR[p.category]
        return (
          <g key={p.dfs_id}>
            <circle
              cx={cx(p.ownership)}
              cy={cy(p.projection)}
              r={cr(p.ev_score)}
              fill={col}
              fillOpacity={0.7}
              stroke={col}
              strokeWidth={1}
            />
          </g>
        )
      })}

      {/* X tick labels */}
      {[0, 10, 20].map(v => (
        <text key={v} x={cx(v)} y={H - 22} textAnchor="middle" fontSize={8} fill="#64748b">{v}%</text>
      ))}
    </svg>
  )
}

// ─── Sort helper ─────────────────────────────────────────────────────────────

type SortKey = 'projection' | 'salary' | 'ownership' | 'ev_score'

function sortPlayers(players: EVPlayer[], key: SortKey, dir: 'asc' | 'desc') {
  return [...players].sort((a, b) => {
    const av = a[key] ?? 0, bv = b[key] ?? 0
    return dir === 'asc' ? av - bv : bv - av
  })
}

// ─── Table header ─────────────────────────────────────────────────────────────

function TH({ children, sortable, active, dir, onClick }: {
  children: React.ReactNode; sortable?: boolean; active?: boolean; dir?: 'asc' | 'desc'; onClick?: () => void
}) {
  return (
    <th
      onClick={sortable ? onClick : undefined}
      className={`px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-[0.05em] border-b border-surface-border whitespace-nowrap select-none ${sortable ? 'cursor-pointer' : 'cursor-default'} ${active ? 'text-primary' : 'text-text-muted'}`}
    >
      {children}{sortable && <span className="ml-1">{active ? (dir === 'desc' ? '↓' : '↑') : '⇅'}</span>}
    </th>
  )
}

// ─── Summary card ─────────────────────────────────────────────────────────────

function SummaryCard({ label, value, icon }: { label: string; value: number; icon: string }) {
  return (
    <div className="bg-surface-raised border border-surface-border rounded-xl px-5 py-4 flex-1 min-w-0">
      <div className="flex justify-between items-start">
        <div>
          <div className="text-[10px] text-text-muted uppercase font-semibold tracking-[0.05em]">{label}</div>
          <div className="text-[28px] font-extrabold text-text-primary mt-1.5">{value}</div>
        </div>
        <span className="text-xl opacity-60">{icon}</span>
      </div>
    </div>
  )
}

// ─── Shared class strings ────────────────────────────────────────────────────

const selCls = 'bg-surface-overlay text-text-primary border border-surface-border rounded px-2.5 py-1.5 text-sm outline-none focus:border-primary cursor-pointer'
const inpCls = 'w-full px-3 py-1.5 bg-surface-base border border-surface-border rounded text-text-primary text-xs outline-none focus:border-primary placeholder:text-text-muted'

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function EVModelingPage() {
  const fileRef = useRef<HTMLInputElement>(null)
  const [site, setSite] = useState<'DK' | 'FD'>('DK')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [players, setPlayers] = useState<EVPlayer[]>([])
  const [tab, setTab] = useState<FilterTab>('All')
  const [search, setSearch] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('ev_score')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  // Slate auto-load
  const [file, setFile] = useState<File | null>(null)
  const { slates, selectedSlate, setSelectedId, slateFile, loading: slateLoading } = useLatestSlate()
  const { players: injuredPlayers } = useInjuryEvents()

  // Build fast lookup: lowercase name → status string
  const injuryMap = new Map<string, string>(
    injuredPlayers.map(p => [p.player_name.toLowerCase(), p.status.toUpperCase()])
  )
  const autoRanRef = useRef<string | null>(null)
  useEffect(() => {
    if (!slateFile) return
    setFile(slateFile)
    if (autoRanRef.current === slateFile.name) return
    autoRanRef.current = slateFile.name
    handleRunEV(slateFile)
  }, [slateFile]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleRunEV = async (f: File) => {
    setLoading(true)
    setError(null)
    try {
      const res = await runProjections(f, site, 'NBA')
      if (!res.success || !res.data) {
        setError(res.error ?? 'Projection failed')
        return
      }
      setPlayers(computeEV(res.data.projections))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    setFile(f)
    handleRunEV(f)
  }

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const categoryCounts = {
    Value:    players.filter(p => p.category === 'Value').length,
    Leverage: players.filter(p => p.category === 'Leverage').length,
    Chalk:    players.filter(p => p.category === 'Chalk').length,
    Pivot:    players.filter(p => p.category === 'Pivot').length,
  }

  const filtered = sortPlayers(
    players.filter(p => {
      if (tab !== 'All' && p.category !== tab) return false
      if (search && !p.name.toLowerCase().includes(search.toLowerCase())) return false
      return true
    }),
    sortKey, sortDir
  )

  const FILTER_TABS: FilterTab[] = ['All', 'Value', 'Leverage', 'Chalk', 'Pivot']

  return (
    <div className="bg-surface-base min-h-[calc(100vh-48px)] p-6">
      <div className="max-w-[1200px] mx-auto">

        {/* Page header */}
        <div className="flex justify-between items-start mb-5">
          <div>
            <h1 className="m-0 text-[22px] font-extrabold text-text-primary">EV Modeling</h1>
            <p className="m-0 mt-1 text-sm text-text-muted">
              Expected value analysis — identify mispriced players and leverage spots
            </p>
          </div>
          <div className="flex items-center gap-2.5">
            {(['DK', 'FD'] as const).map(s => (
              <button key={s} onClick={() => setSite(s)} className={`px-3.5 py-1.5 rounded text-xs font-bold cursor-pointer border-none transition-colors ${site === s ? 'bg-primary text-white' : 'bg-surface-raised text-text-secondary hover:bg-surface-overlay'}`}>{s === 'DK' ? 'DRAFTKINGS' : 'FANDUEL'} NBA</button>
            ))}
            <label className="px-3.5 py-1.5 rounded text-xs font-semibold cursor-pointer bg-surface-raised text-text-secondary border border-surface-border hover:bg-surface-overlay transition-colors">
              {loading ? 'Loading…' : '↑ Upload Slate'}
              <input ref={fileRef} type="file" accept=".csv" onChange={handleFile} className="hidden" disabled={loading} />
            </label>
          </div>
        </div>

        {/* Slate selector + run */}
        <div className="flex gap-2.5 items-center mb-4">
          <div className="flex-1">
            <SlateSelector
              slates={slates}
              selected={selectedSlate}
              loading={slateLoading}
              onSelect={setSelectedId}
              onFileOverride={f => { setFile(f); handleRunEV(f) }}
            />
          </div>
          <button
            onClick={() => file && handleRunEV(file)}
            disabled={loading || !file}
            className="px-5 py-2 rounded-lg border-none shrink-0 bg-primary text-white font-bold text-sm cursor-pointer hover:bg-primary-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? 'Running…' : 'Run EV Analysis'}
          </button>
        </div>

        {error && (
          <div className="bg-danger-muted border border-danger/40 rounded-lg px-4 py-2.5 mb-4 text-danger text-sm">
            {error}
          </div>
        )}

        {/* Injury banner */}
        {injuredPlayers.length > 0 && (() => {
          const out = injuredPlayers.filter(p => p.status.toUpperCase() === 'OUT')
          const gtd = injuredPlayers.filter(p => ['GTD', 'QUESTIONABLE', 'DOUBTFUL'].includes(p.status.toUpperCase()))
          if (!out.length && !gtd.length) return null
          return (
            <div className="bg-warning-muted border border-warning/40 rounded-lg px-4 py-2.5 mb-4 text-xs">
              <span className="text-warning font-bold mr-2">⚠ Injury Report</span>
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
            </div>
          )
        })()}

        {/* Summary cards */}
        <div className="flex gap-3 mb-5">
          <SummaryCard label="Value Plays"    value={categoryCounts.Value}    icon="⚡" />
          <SummaryCard label="Leverage Spots" value={categoryCounts.Leverage} icon="🎯" />
          <SummaryCard label="Chalk"          value={categoryCounts.Chalk}    icon="📈" />
          <SummaryCard label="Pivots"         value={categoryCounts.Pivot}    icon="△" />
        </div>

        {/* Main content: table + chart */}
        <div className="flex gap-4 items-start">

          {/* Left: Player EV Rankings */}
          <div className="flex-1 min-w-0 bg-surface-raised border border-surface-border rounded-xl overflow-hidden">
            <div className="px-4 py-3.5 border-b border-surface-border flex justify-between items-center">
              <span className="text-sm font-bold text-text-primary">Player EV Rankings</span>
              <div className="flex gap-1">
                {FILTER_TABS.map(t => (
                  <button key={t} onClick={() => setTab(t)} className={`px-2.5 py-1 rounded text-[11px] font-semibold cursor-pointer border-none transition-colors ${tab === t ? 'bg-primary text-white' : 'bg-transparent text-text-muted hover:text-text-secondary'}`}>{t}</button>
                ))}
              </div>
            </div>

            <div className="px-4 py-2.5">
              <input
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Search players…"
                className={inpCls}
              />
            </div>

            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr>
                    <TH>Player</TH>
                    <TH>Pos</TH>
                    <TH>Team</TH>
                    <TH sortable active={sortKey==='salary'}    dir={sortDir} onClick={() => handleSort('salary')}>Salary</TH>
                    <TH sortable active={sortKey==='projection'} dir={sortDir} onClick={() => handleSort('projection')}>Proj</TH>
                    <TH sortable active={sortKey==='ownership'} dir={sortDir} onClick={() => handleSort('ownership')}>Own%</TH>
                    <TH sortable active={sortKey==='ev_score'}  dir={sortDir} onClick={() => handleSort('ev_score')}>EV Score</TH>
                    <TH>Category</TH>
                  </tr>
                </thead>
                <tbody>
                  {!players.length ? (
                    <tr>
                      <td colSpan={8} className="px-8 py-8 text-center text-text-muted text-sm">
                        Upload a slate CSV to compute EV rankings
                      </td>
                    </tr>
                  ) : filtered.length === 0 ? (
                    <tr>
                      <td colSpan={8} className="px-6 py-6 text-center text-text-muted text-sm">No matching players</td>
                    </tr>
                  ) : filtered.map((p, i) => {
                    const injStatus = injuryMap.get(p.name.toLowerCase())
                    const isOut = injStatus === 'OUT'
                    const isGtd = injStatus && !isOut
                    return (
                    <tr key={p.dfs_id} className={`${isOut ? 'bg-danger-muted' : i % 2 === 0 ? 'bg-transparent' : 'bg-surface-base/30'}`}>
                      <td className="px-3 py-2 text-sm font-semibold whitespace-nowrap">
                        <span className={isOut ? 'text-danger' : 'text-text-primary'}>{p.name}</span>
                        {isOut && <span className="ml-1.5 text-[10px] font-bold bg-danger-muted text-danger px-1 py-0.5 rounded">OUT</span>}
                        {isGtd && <span className="ml-1.5 text-[10px] font-bold bg-warning-muted text-warning px-1 py-0.5 rounded">{injStatus}</span>}
                      </td>
                      <td className="px-3 py-2 text-xs text-text-muted">{p.position}</td>
                      <td className="px-3 py-2 text-xs text-text-secondary">{p.team}</td>
                      <td className="px-3 py-2 text-xs text-text-secondary">{formatSalary(p.salary)}</td>
                      <td className="px-3 py-2 text-sm font-bold text-[#22d3ee]">{p.projection.toFixed(1)}</td>
                      <td className="px-3 py-2 text-xs text-text-secondary">{p.ownership.toFixed(1)}%</td>
                      <td className="px-3 py-2 text-sm font-bold text-warning">{p.ev_score.toFixed(1)}</td>
                      <td className="px-3 py-2"><CategoryBadge cat={p.category} /></td>
                    </tr>
                    )
                  })}
                </tbody>
              </table>
              {filtered.length > 0 && (
                <div className="px-4 py-2 border-t border-surface-border text-[11px] text-text-muted text-right">
                  {filtered.length} rows
                </div>
              )}
            </div>
          </div>

          {/* Right: Bubble chart */}
          <div className="w-[440px] shrink-0 bg-surface-raised border border-surface-border rounded-xl overflow-hidden">
            <div className="px-4 py-3.5 border-b border-surface-border">
              <div className="text-sm font-bold text-text-primary">Ownership vs Projection</div>
              <div className="text-[11px] text-text-muted mt-0.5">Bubble size = EV score</div>
            </div>
            <div className="p-4 min-h-[280px]">
              <BubbleChart players={filtered.slice(0, 40)} />
            </div>
            {/* Legend */}
            <div className="px-4 pb-3.5 flex flex-wrap gap-2">
              {(Object.keys(CAT_COLOR) as Category[]).map(cat => (
                <div key={cat} className="flex items-center gap-1.5">
                  <div className="w-2.5 h-2.5 rounded-full" style={{ background: CAT_COLOR[cat] }} />
                  <span className="text-[11px] text-text-muted">{cat}</span>
                  <span className="text-[11px] text-text-muted ml-0.5">{categoryCounts[cat]} players</span>
                </div>
              ))}
            </div>
          </div>

        </div>
      </div>
    </div>
  )
}
