'use client'

import { useRef, useState, useEffect } from 'react'
import Link from 'next/link'
import { runProjections, downloadFile, toCsv, type PlayerProjection } from '@/lib/api'
import { formatSalary } from '@/lib/utils'
import { useLatestSlate } from '@/hooks/useLatestSlate'
import { SlateSelector } from '@/components/shared/SlateSelector'
import { SkeletonTable } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

type SortKey = keyof PlayerProjection
type SortDir = 'asc' | 'desc'

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function valueTier(v: number): string {
  if (v >= 5) return 'elite'
  if (v >= 4) return 'strong'
  if (v >= 3) return 'solid'
  if (v >= 2) return 'weak'
  return 'punt'
}

function tierColor(tier: string): string {
  switch (tier) {
    case 'elite': return 'text-green-400'
    case 'strong': return 'text-lime-500'
    case 'solid': return 'text-yellow-400'
    case 'weak': return 'text-orange-500'
    default: return 'text-gray-500'
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared class strings
// ─────────────────────────────────────────────────────────────────────────────

const lblCls = 'block text-[11px] text-text-muted uppercase mb-1 font-semibold'
const selCls = 'bg-surface-overlay text-text-primary border border-surface-border rounded px-2.5 py-1.5 text-sm outline-none focus:border-primary cursor-pointer'
const inpCls = 'bg-surface-overlay text-text-primary border border-surface-border rounded px-2.5 py-1.5 text-sm outline-none focus:border-primary placeholder:text-text-muted'
const priBtnCls = 'bg-primary text-white border-none rounded px-4 py-1.5 text-sm font-semibold cursor-pointer hover:bg-primary-hover transition-colors disabled:opacity-50'
const secBtnCls = 'bg-surface-overlay text-text-secondary border border-surface-border rounded px-3.5 py-1.5 text-sm cursor-pointer hover:bg-surface-border transition-colors'
const thClsBase = 'px-3 py-2 text-left border-b-2 border-surface-border text-xs font-semibold whitespace-nowrap cursor-pointer select-none'
const tdCls = 'px-3 py-2 text-text-secondary border-b border-surface-border/40 text-sm'

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export default function ProjectionsPage() {
  const fileRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [site, setSite] = useState<'DK' | 'FD'>('DK')
  const [sport, setSport] = useState<'NBA' | 'NFL'>('NBA')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [rateLimited, setRateLimited] = useState(false)
  const [projections, setProjections] = useState<PlayerProjection[]>([])
  const [stats, setStats] = useState<{ total_players: number; avg_projection: number; site: string } | null>(null)
  const [sortKey, setSortKey] = useState<SortKey>('projection')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [posFilter, setPosFilter] = useState<string>('ALL')
  const [search, setSearch] = useState('')

  // Slate auto-load
  const { slates, selectedSlate, setSelectedId, slateFile, loading: slateLoading } = useLatestSlate()
  const autoRanRef = useRef<string | null>(null)
  useEffect(() => {
    if (!slateFile) return
    setFile(slateFile)
    if (autoRanRef.current === slateFile.name) return
    autoRanRef.current = slateFile.name
    setLoading(true)
    setError(null)
    setRateLimited(false)
    setProjections([])
    setStats(null)
    runProjections(slateFile, site, sport).then(res => {
      setLoading(false)
      if (!res.success || !res.data) {
        if (res.rateLimited) { setRateLimited(true); return }
        setError(res.error ?? 'Unknown error'); return
      }
      setProjections(res.data.projections)
      setStats(res.data.stats)
    })
  }, [slateFile]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Sort & filter ────────────────────────────────────────────────────────

  const sorted = [...projections]
    .filter(p => {
      if (posFilter !== 'ALL' && !p.position.toUpperCase().includes(posFilter)) return false
      if (search && !p.name.toLowerCase().includes(search.toLowerCase())) return false
      return true
    })
    .sort((a, b) => {
      const va = a[sortKey] as number | string
      const vb = b[sortKey] as number | string
      const cmp = typeof va === 'number' ? va - (vb as number) : String(va).localeCompare(String(vb))
      return sortDir === 'desc' ? -cmp : cmp
    })

  const positions = ['ALL', ...Array.from(new Set(projections.flatMap(p => p.position.split('/')))).sort()]

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleSort = (key: SortKey) => {
    if (key === sortKey) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const handleRun = async () => {
    if (!file) return
    setLoading(true)
    setError(null)
    setRateLimited(false)
    setProjections([])
    setStats(null)
    const res = await runProjections(file, site, sport)
    setLoading(false)
    if (!res.success || !res.data) {
      if (res.rateLimited) { setRateLimited(true); return }
      setError(res.error ?? 'Unknown error')
      return
    }
    setProjections(res.data.projections)
    setStats(res.data.stats)
  }

  const handleDownload = () => {
    if (!projections.length) return
    const rows = sorted.map(p => ({
      Name: p.name,
      Position: p.position,
      Team: p.team,
      Opp: p.opponent,
      Salary: p.salary,
      Proj: p.projection,
      Floor: p.floor,
      Ceiling: p.ceiling,
      StdDev: p.std_dev,
      Value: p.value,
      'Own%': p.ownership,
    }))
    const dateStr = new Date().toISOString().slice(0, 10)
    downloadFile(toCsv(rows), `projections_${site}_${sport}_${dateStr}.csv`)
  }

  // ── Column header helper ──────────────────────────────────────────────────

  const Th = ({ label, field }: { label: string; field: SortKey }) => (
    <th
      onClick={() => handleSort(field)}
      className={`${thClsBase} ${sortKey === field ? 'text-primary' : 'text-text-muted'}`}
    >
      {label} {sortKey === field ? (sortDir === 'desc' ? '▼' : '▲') : ''}
    </th>
  )

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="bg-surface-base min-h-[calc(100vh-48px)] p-6">
      <div className="max-w-[1200px] mx-auto">
        <div className="mb-5">
          <h1 className="m-0 text-[22px] font-extrabold text-text-primary">Projections</h1>
          <p className="m-0 mt-1 text-sm text-text-muted">Upload a DK/FD salary CSV to generate player projections from the canonical engine.</p>
        </div>

        {/* Slate selector */}
        <SlateSelector
          slates={slates}
          selected={selectedSlate}
          loading={slateLoading}
          onSelect={setSelectedId}
          onFileOverride={f => setFile(f)}
        />

        {/* Upload Card */}
        <div className="bg-surface-raised border border-surface-border rounded-xl mb-6 overflow-hidden">
          <div className="px-5 py-3.5 border-b border-surface-border text-sm font-bold text-text-primary">Upload Salary Export</div>
          <div className="px-5 py-4 flex flex-wrap gap-3 items-end">

            {/* Site */}
            <div>
              <label className={lblCls}>Site</label>
              <select value={site} onChange={e => setSite(e.target.value as 'DK' | 'FD')} className={selCls}>
                <option value="DK">DraftKings</option>
                <option value="FD">FanDuel</option>
              </select>
            </div>

            {/* Sport */}
            <div>
              <label className={lblCls}>Sport</label>
              <select value={sport} onChange={e => setSport(e.target.value as 'NBA' | 'NFL')} className={selCls}>
                <option value="NBA">NBA</option>
                <option value="NFL">NFL</option>
              </select>
            </div>

            {/* File */}
            <div>
              <label className={lblCls}>Salary CSV</label>
              <input
                ref={fileRef}
                type="file"
                accept=".csv"
                onChange={e => setFile(e.target.files?.[0] ?? null)}
                className="hidden"
              />
              <button onClick={() => fileRef.current?.click()} className={secBtnCls}>
                {file ? file.name : 'Choose File'}
              </button>
            </div>

            {/* Run */}
            <button
              onClick={handleRun}
              disabled={!file || loading}
              className={priBtnCls}
            >
              {loading ? 'Running...' : 'Run Projections'}
            </button>
          </div>
        </div>

        {/* Rate-limit upgrade banner */}
        {rateLimited && (
          <div className="bg-warning-muted border border-warning/40 rounded-lg px-4 py-4 mb-5 flex flex-col sm:flex-row sm:items-center gap-3">
            <div className="flex-1">
              <p className="text-sm font-bold text-warning mb-0.5">Daily projection limit reached</p>
              <p className="text-xs text-text-secondary">Free accounts are limited to 5 projection runs per day. Upgrade to Pro for unlimited runs.</p>
            </div>
            <Link
              href="/billing"
              className="shrink-0 px-4 py-2 rounded bg-warning text-white text-sm font-bold hover:bg-warning/90 transition-colors text-center"
            >
              Upgrade to Pro
            </Link>
          </div>
        )}

        {/* Generic error */}
        {error && !rateLimited && (
          <div className="bg-danger-muted border border-danger/40 rounded-lg px-4 py-3 text-danger mb-5">
            {error}
          </div>
        )}

        {/* Stats strip */}
        {stats && (
          <div className="flex gap-4 flex-wrap mb-5">
            {[
              { label: 'Players', value: String(stats.total_players) },
              { label: 'Avg Proj', value: stats.avg_projection.toFixed(1) },
              { label: 'Site', value: stats.site },
            ].map(s => (
              <div key={s.label} className="bg-surface-raised border border-surface-border rounded-lg px-5 py-2.5 flex flex-col items-center">
                <span className="text-[11px] text-text-muted uppercase">{s.label}</span>
                <span className="text-xl font-bold text-text-primary">{s.value}</span>
              </div>
            ))}
          </div>
        )}

        {/* Skeleton while loading */}
        {loading && projections.length === 0 && (
          <div className="bg-surface-raised border border-surface-border rounded-xl p-4">
            <SkeletonTable rows={10} cols={10} />
          </div>
        )}

        {/* Empty state — no projections run yet */}
        {!loading && !error && projections.length === 0 && (
          <div className="bg-surface-raised border border-surface-border rounded-xl">
            <EmptyState
              icon="📊"
              title="No projections yet"
              description="Select a slate or upload a salary CSV above, then click Run Projections."
              action={file ? { label: 'Run Projections', onClick: handleRun } : undefined}
            />
          </div>
        )}

        {/* Projections table */}
        {projections.length > 0 && (
          <div className="bg-surface-raised border border-surface-border rounded-xl overflow-hidden">
            <div className="px-4 py-3 flex gap-2.5 flex-wrap border-b border-surface-border items-center">
              <span className="text-sm font-bold text-text-primary">{sorted.length} Players</span>

              {/* Search */}
              <input
                placeholder="Search player..."
                value={search}
                onChange={e => setSearch(e.target.value)}
                className={`${inpCls} w-[180px]`}
              />

              {/* Position filter */}
              <div className="flex gap-1.5 flex-wrap">
                {positions.map(pos => (
                  <button
                    key={pos}
                    onClick={() => setPosFilter(pos)}
                    className={`px-2.5 py-0.5 rounded-full text-xs border-none cursor-pointer transition-colors ${posFilter === pos ? 'bg-primary text-white' : 'bg-surface-overlay text-text-muted hover:bg-surface-border'}`}
                  >
                    {pos}
                  </button>
                ))}
              </div>

              {/* Download */}
              <button onClick={handleDownload} className={`${secBtnCls} ml-auto`}>
                Download CSV
              </button>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr>
                    <Th label="Name" field="name" />
                    <Th label="Pos" field="position" />
                    <Th label="Team" field="team" />
                    <Th label="Opp" field="opponent" />
                    <Th label="Salary" field="salary" />
                    <Th label="Proj" field="projection" />
                    <Th label="Floor" field="floor" />
                    <Th label="Ceiling" field="ceiling" />
                    <Th label="StdDev" field="std_dev" />
                    <Th label="Value" field="value" />
                    <Th label="Own%" field="ownership" />
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((p, i) => {
                    const tier = valueTier(p.value)
                    return (
                      <tr
                        key={p.dfs_id || p.name}
                        className={`transition-colors ${i % 2 === 0 ? 'bg-surface-raised' : 'bg-surface-base/30'}`}
                      >
                        <td className={tdCls}><span className="text-text-primary font-medium">{p.name}</span></td>
                        <td className={tdCls}><span className="text-[#a78bfa] text-[11px]">{p.position}</span></td>
                        <td className={tdCls}>{p.team}</td>
                        <td className={tdCls}>{p.opponent}</td>
                        <td className={tdCls}>{formatSalary(p.salary)}</td>
                        <td className={`${tdCls} font-bold text-primary`}>{p.projection.toFixed(1)}</td>
                        <td className={`${tdCls} text-text-muted`}>{p.floor.toFixed(1)}</td>
                        <td className={`${tdCls} text-success`}>{p.ceiling.toFixed(1)}</td>
                        <td className={`${tdCls} text-text-muted`}>{p.std_dev.toFixed(2)}</td>
                        <td className={tdCls}>
                          <span className={`font-semibold ${tierColor(tier)}`}>
                            {p.value.toFixed(2)}x
                          </span>
                        </td>
                        <td className={`${tdCls} text-text-muted`}>{p.ownership.toFixed(1)}%</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}


// ─────────────────────────────────────────────────────────────────────────────
// Types
