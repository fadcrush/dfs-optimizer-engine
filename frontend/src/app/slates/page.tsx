'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { getSlates, uploadSlate, deleteSlate, getSlatePlayers, refreshInjuries } from '@/lib/api/slates'
import type { SlateListItem, SlatePlayer, InjurySummary } from '@/lib/api/slates'
import { InjuryBadge } from '@/components/shared/InjuryBadge'
import { InjuryAlertBanner } from '@/components/shared/InjuryAlertBanner'
import { InjurySummaryPanel } from '@/components/shared/InjurySummaryPanel'

// ─────────────────────────────────────────────────────────────────────────────
// Mock data fallback
// ─────────────────────────────────────────────────────────────────────────────

const MOCK_SLATES: SlateListItem[] = [
  { id: 's1', platform: 'draftkings', sport: 'nba', date: '2025-01-15', lock_time: '2025-01-15T19:00:00Z', slate_type: 'Main', player_count: 84, created_at: new Date().toISOString(), status: 'active' },
  { id: 's2', platform: 'fanduel', sport: 'nba', date: '2025-01-15', lock_time: '2025-01-15T19:00:00Z', slate_type: 'Main', player_count: 72, created_at: new Date(Date.now() - 3600000).toISOString(), status: 'active' },
  { id: 's3', platform: 'draftkings', sport: 'nfl', date: '2025-01-12', lock_time: '2025-01-12T13:00:00Z', slate_type: 'Main', player_count: 120, created_at: new Date(Date.now() - 86400000).toISOString(), status: 'locked' },
]

const EMPTY_SUMMARY: InjurySummary = {
  out_count: 0, questionable_count: 0, doubtful_count: 0, probable_count: 0,
  out_players: [], questionable_players: [], doubtful_players: [],
  all_injuries: [], changed_since_export: 0, last_updated: null,
}

// ─────────────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────────────

function StatusPill({ status }: { status: string }) {
  const cls: Record<string, string> = {
    active:     'bg-success-muted text-success border-success/30',
    locked:     'bg-warning-muted text-warning border-warning/30',
    processing: 'bg-primary-muted text-primary border-primary/30',
  }
  const base = cls[status] ?? 'bg-surface-overlay text-text-secondary border-surface-border'
  return (
    <span className={`inline-block rounded-full px-2.5 py-0.5 text-[11px] font-bold capitalize border ${base}`}>
      {status}
    </span>
  )
}

interface UploadModalProps { onClose: () => void; onSuccess: () => void }

function UploadModal({ onClose, onSuccess }: UploadModalProps) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [platform, setPlatform] = useState('draftkings')
  const [sport, setSport] = useState('nba')
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const handleUpload = async () => {
    if (!file) return
    setUploading(true); setErr(null)
    try {
      const res = await uploadSlate(file, platform, sport)
      if (res.success) { onSuccess() } else { setErr(res.error ?? 'Upload failed') }
    } catch { setErr('Network error') }
    finally { setUploading(false) }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
      <div className="bg-surface-overlay border border-surface-border rounded-xl w-full max-w-[440px]">
        <div className="px-5 py-3.5 border-b border-surface-border flex justify-between items-center">
          <span className="font-bold text-text-primary text-[15px]">Upload Slate</span>
          <button onClick={onClose} className="text-text-muted hover:text-text-primary text-lg cursor-pointer bg-transparent border-none">&#x2715;</button>
        </div>
        <div className="p-5 flex flex-col gap-3.5">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={lblCls}>Platform</label>
              <select value={platform} onChange={e => setPlatform(e.target.value)} className={selCls}>
                <option value="draftkings">DraftKings</option>
                <option value="fanduel">FanDuel</option>
              </select>
            </div>
            <div>
              <label className={lblCls}>Sport</label>
              <select value={sport} onChange={e => setSport(e.target.value)} className={selCls}>
                <option value="nba">NBA</option>
                <option value="nfl">NFL</option>
              </select>
            </div>
          </div>
          <div>
            <label className={lblCls}>CSV File</label>
            <div
              onClick={() => fileRef.current?.click()}
              className={`border-2 border-dashed border-surface-border rounded-lg py-5 px-4 text-center cursor-pointer text-sm transition-colors hover:border-primary ${file ? 'text-primary' : 'text-text-muted'}`}
            >
              {file ? file.name : '+ Click to select CSV'}
            </div>
            <input ref={fileRef} type="file" accept=".csv" className="hidden"
              onChange={e => setFile(e.target.files?.[0] ?? null)} />
          </div>
          {err && <div className="text-danger text-xs">{err}</div>}
          <div className="flex gap-2 pt-1">
            <button onClick={onClose} className={secBtnCls}>Cancel</button>
            <button onClick={handleUpload} disabled={!file || uploading}
              className={`${priBtnCls} flex-1 disabled:opacity-50`}>
              {uploading ? 'Uploading\u2026' : 'Upload'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

interface ConfirmModalProps { slate: SlateListItem; deleting: boolean; onConfirm: () => void; onCancel: () => void }

function ConfirmModal({ slate, deleting, onConfirm, onCancel }: ConfirmModalProps) {
  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
      <div className="bg-surface-overlay border border-surface-border rounded-xl w-full max-w-[400px] p-6">
        <h3 className="m-0 mb-2 text-text-primary text-base font-bold">Delete Slate</h3>
        <p className="m-0 mb-5 text-text-secondary text-sm">
          Delete{' '}
          <strong className="text-text-primary">
            {slate.platform.toUpperCase()} {slate.sport.toUpperCase()}
          </strong>{' '}
          slate from {slate.date}? This cannot be undone.
        </p>
        <div className="flex gap-2">
          <button onClick={onCancel} className={secBtnCls}>Cancel</button>
          <button onClick={onConfirm} disabled={deleting}
            className="flex-1 bg-danger text-white border-none rounded px-3.5 py-1.5 text-sm font-semibold cursor-pointer disabled:opacity-50 hover:bg-danger/90 transition-colors">
            {deleting ? 'Deleting\u2026' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Player table sub-component (shown when a slate is selected)
// ─────────────────────────────────────────────────────────────────────────────

type PlayerSortKey = 'name' | 'position' | 'team' | 'salary' | 'fppg' | 'injury_status'

function SlatePlayerTable({ players, search }: { players: SlatePlayer[]; search: string }) {
  const [sortKey, setSortKey] = useState<PlayerSortKey>('salary')
  const [sortDesc, setSortDesc] = useState(true)
  const [posFilter, setPosFilter] = useState('')

  const positions = Array.from(new Set(players.map(p => p.position).filter(Boolean))).sort()

  const filtered = players
    .filter(p => {
      if (posFilter && p.position !== posFilter) return false
      if (search.trim()) {
        const q = search.trim().toLowerCase()
        return p.name.toLowerCase().includes(q) || p.team.toLowerCase().includes(q)
      }
      return true
    })
    .sort((a, b) => {
      switch (sortKey) {
        case 'salary': return sortDesc ? b.salary - a.salary : a.salary - b.salary
        case 'fppg': return sortDesc ? b.fppg - a.fppg : a.fppg - b.fppg
        case 'name': return sortDesc ? b.name.localeCompare(a.name) : a.name.localeCompare(b.name)
        case 'team': return sortDesc ? b.team.localeCompare(a.team) : a.team.localeCompare(b.team)
        case 'position': return sortDesc ? b.position.localeCompare(a.position) : a.position.localeCompare(b.position)
        case 'injury_status': {
          const order: Record<string, number> = { OUT: 0, O: 0, DOUBTFUL: 1, D: 1, QUESTIONABLE: 2, Q: 2, GTD: 2, PROBABLE: 3, P: 3 }
          const av = order[a.injury_status?.toUpperCase()] ?? 9
          const bv = order[b.injury_status?.toUpperCase()] ?? 9
          return sortDesc ? av - bv : bv - av
        }
        default: return 0
      }
    })

  const toggleSort = (key: PlayerSortKey) => {
    if (sortKey === key) setSortDesc(!sortDesc)
    else { setSortKey(key); setSortDesc(true) }
  }
  const sortInd = (key: PlayerSortKey) => sortKey === key ? (sortDesc ? ' \u25BC' : ' \u25B2') : ''

  return (
    <div className="border border-surface-border rounded-xl overflow-hidden bg-surface-raised">
      {/* Toolbar */}
      <div className="px-4 py-2.5 border-b border-surface-border flex items-center gap-2 flex-wrap">
        <span className="text-sm font-bold text-text-primary">{filtered.length} Players</span>
        {positions.length > 0 && (
          <select value={posFilter} onChange={e => setPosFilter(e.target.value)}
            className="bg-surface-base text-text-primary border border-surface-border rounded px-2 py-1 text-xs outline-none focus:border-primary cursor-pointer">
            <option value="">All Positions</option>
            {positions.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        )}
      </div>

      {/* Table */}
      <div className="overflow-x-auto max-h-[480px] overflow-y-auto">
        <table className="w-full border-collapse text-sm">
          <thead className="sticky top-0 z-10 bg-surface-base">
            <tr>
              <th className={`${thCls} cursor-pointer hover:text-text-primary`} onClick={() => toggleSort('injury_status')}>
                Injury{sortInd('injury_status')}
              </th>
              <th className={`${thCls} cursor-pointer hover:text-text-primary`} onClick={() => toggleSort('name')}>
                Name{sortInd('name')}
              </th>
              <th className={`${thCls} cursor-pointer hover:text-text-primary`} onClick={() => toggleSort('position')}>
                Pos{sortInd('position')}
              </th>
              <th className={`${thCls} cursor-pointer hover:text-text-primary`} onClick={() => toggleSort('team')}>
                Team{sortInd('team')}
              </th>
              <th className={`${thCls} text-right cursor-pointer hover:text-text-primary`} onClick={() => toggleSort('salary')}>
                Salary{sortInd('salary')}
              </th>
              <th className={`${thCls} text-right cursor-pointer hover:text-text-primary`} onClick={() => toggleSort('fppg')}>
                FPPG{sortInd('fppg')}
              </th>
              <th className={`${thCls}`}>Opp</th>
              <th className={`${thCls}`}>Game</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((p, i) => {
              const isOut = ['OUT', 'O'].includes(p.injury_status?.toUpperCase())
              return (
                <tr key={`${p.name}-${p.team}-${i}`}
                  className={`transition-colors ${isOut ? 'opacity-50' : 'hover:bg-surface-overlay'} ${i % 2 === 0 ? 'bg-surface-raised' : 'bg-surface-base/30'}`}
                >
                  <td className={tdCls}>
                    {p.injury_status && !['', 'ACTIVE'].includes(p.injury_status.toUpperCase()) && (
                      <div className="flex items-center gap-1">
                        <InjuryBadge status={p.injury_status} detail={p.injury_detail} compact />
                        {p.status_changed && (
                          <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" title="Status changed since export" />
                        )}
                      </div>
                    )}
                  </td>
                  <td className={`${tdCls} font-semibold text-text-primary ${isOut ? 'line-through' : ''}`}>{p.name}</td>
                  <td className={tdCls}>{p.position}</td>
                  <td className={tdCls}>{p.team}</td>
                  <td className={`${tdCls} text-right font-mono`}>${(p.salary ?? 0).toLocaleString()}</td>
                  <td className={`${tdCls} text-right font-mono`}>{p.fppg > 0 ? p.fppg.toFixed(1) : '\u2014'}</td>
                  <td className={tdCls}>{p.opponent}</td>
                  <td className={`${tdCls} text-text-muted`}>{p.game}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared class strings
// ─────────────────────────────────────────────────────────────────────────────

const lblCls = 'block text-[11px] text-text-muted uppercase mb-1 font-semibold'
const selCls = 'w-full bg-surface-overlay text-text-primary border border-surface-border rounded px-2.5 py-1.5 text-sm outline-none focus:border-primary'
const inpCls = 'bg-surface-overlay text-text-primary border border-surface-border rounded px-2.5 py-1.5 text-sm outline-none focus:border-primary placeholder:text-text-muted'
const priBtnCls = 'bg-primary text-white border-none rounded px-4 py-1.5 text-sm font-semibold cursor-pointer hover:bg-primary-hover transition-colors'
const secBtnCls = 'flex-1 bg-surface-raised text-text-secondary border border-surface-border rounded px-3.5 py-1.5 text-sm cursor-pointer hover:bg-surface-overlay transition-colors'
const thCls = 'px-3 py-2 text-left border-b-2 border-surface-border text-[11px] text-text-muted font-bold uppercase whitespace-nowrap select-none'
const tdCls = 'px-3 py-2 text-text-secondary border-b border-surface-border/40 text-sm'

// ─────────────────────────────────────────────────────────────────────────────
// Main page
// ─────────────────────────────────────────────────────────────────────────────

export default function SlatesPage() {
  const [slates, setSlates] = useState<SlateListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [usingMock, setUsingMock] = useState(false)
  const [showUpload, setShowUpload] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<SlateListItem | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null)

  // Player + injury state for selected slate
  const [slatePlayers, setSlatePlayers] = useState<SlatePlayer[]>([])
  const [injurySummary, setInjurySummary] = useState<InjurySummary>(EMPTY_SUMMARY)
  const [playersLoading, setPlayersLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  const showToast = (msg: string, ok = true) => {
    setToast({ msg, ok })
    setTimeout(() => setToast(null), 3000)
  }

  const loadSlates = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getSlates()
      setSlates(res.slates ?? [])
      setUsingMock(false)
    } catch {
      setSlates(MOCK_SLATES)
      setUsingMock(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadSlates() }, [loadSlates])

  // Fetch players + injury data when a slate is selected
  const loadSlatePlayers = useCallback(async (slateId: string) => {
    setPlayersLoading(true)
    try {
      const res = await getSlatePlayers(slateId)
      setSlatePlayers(res.players ?? [])
      setInjurySummary(res.injury_summary ?? EMPTY_SUMMARY)
    } catch {
      setSlatePlayers([])
      setInjurySummary(EMPTY_SUMMARY)
    } finally {
      setPlayersLoading(false)
    }
  }, [])

  const handleSelectSlate = (slateId: string) => {
    setSelectedId(slateId)
    loadSlatePlayers(slateId)
  }

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      await refreshInjuries()
      showToast('Injury refresh started')
      // Re-fetch players after a brief delay for the background refresh
      if (selectedId) {
        setTimeout(() => loadSlatePlayers(selectedId), 3000)
      }
    } catch {
      showToast('Refresh failed', false)
    } finally {
      setRefreshing(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      const res = await deleteSlate(deleteTarget.id)
      if (res.success) {
        setSlates(prev => prev.filter(s => s.id !== deleteTarget.id))
        if (selectedId === deleteTarget.id) {
          setSelectedId(null)
          setSlatePlayers([])
          setInjurySummary(EMPTY_SUMMARY)
        }
        showToast('Slate deleted')
      } else {
        showToast(res.error ?? 'Delete failed', false)
      }
    } catch {
      showToast('Network error', false)
    } finally {
      setDeleting(false)
      setDeleteTarget(null)
    }
  }

  const filtered = slates.filter(s =>
    s.platform.toLowerCase().includes(search.toLowerCase()) ||
    s.sport.toLowerCase().includes(search.toLowerCase()) ||
    s.date.includes(search)
  )

  const injuryTotal = injurySummary.out_count + injurySummary.questionable_count + injurySummary.doubtful_count

  return (
    <div className="bg-surface-base min-h-[calc(100vh-48px)] p-6">
      <div className="max-w-[1200px] mx-auto">

        {/* Toast */}
        {toast && (
          <div className={`fixed top-5 right-5 z-[100] px-4 py-2.5 rounded-lg text-sm font-semibold border ${toast.ok ? 'bg-success-muted border-success/40 text-success' : 'bg-danger-muted border-danger/40 text-danger'}`}>
            {toast.msg}
          </div>
        )}

        {/* Header */}
        <div className="flex justify-between items-start mb-5">
          <div>
            <h1 className="m-0 text-[22px] font-extrabold text-text-primary">Slates</h1>
            <p className="m-0 mt-1 text-sm text-text-muted">
              Manage imported DraftKings and FanDuel slates
            </p>
          </div>
          <button onClick={() => setShowUpload(true)} className={priBtnCls}>+ Upload Slate</button>
        </div>

        {/* Mock banner */}
        {usingMock && (
          <div className="bg-warning-muted border border-warning/30 rounded-lg px-3.5 py-2 text-warning text-xs mb-4">
            Showing mock data — backend unreachable
          </div>
        )}

        {/* Summary cards */}
        <div className="flex gap-3.5 flex-wrap mb-5">
          {[
            { label: 'Total Slates', value: slates.length },
            { label: 'Active', value: slates.filter(s => s.status === 'active').length },
            { label: 'NBA', value: slates.filter(s => s.sport === 'nba').length },
            { label: 'NFL', value: slates.filter(s => s.sport === 'nfl').length },
            ...(selectedId && injuryTotal > 0 ? [{
              label: 'Injuries',
              value: `${injurySummary.out_count} OUT / ${injurySummary.questionable_count} Q`,
            }] : []),
          ].map(c => (
            <div key={c.label} className="bg-surface-raised border border-surface-border rounded-lg px-5 py-2.5 flex flex-col items-center">
              <span className="text-[11px] text-text-muted uppercase font-semibold">{c.label}</span>
              <span className={`text-[22px] font-bold ${c.label === 'Injuries' ? 'text-red-400 text-[16px]' : 'text-text-primary'}`}>
                {c.value}
              </span>
            </div>
          ))}
        </div>

        {/* Table card */}
        <div className="bg-surface-raised border border-surface-border rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-surface-border flex gap-2.5 items-center flex-wrap">
            <span className="text-sm font-bold text-text-primary">
              {filtered.length} Slate{filtered.length !== 1 ? 's' : ''}
            </span>
            <input
              placeholder="Search\u2026"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className={`${inpCls} w-[200px] ml-2`}
            />
          </div>

          {loading ? (
            <div className="py-10 text-center text-text-muted">Loading slates\u2026</div>
          ) : filtered.length === 0 ? (
            <div className="py-10 text-center text-text-muted">
              {slates.length === 0
                ? 'No slates uploaded yet. Click "+ Upload Slate" to get started.'
                : 'No slates match your search.'}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr>
                    {['', 'Platform', 'Sport', 'Date', 'Type', 'Players', 'Status', 'Imported', ''].map((h, i) => (
                      <th key={i} className={`${thCls} ${i >= 5 && i !== 8 ? 'text-right' : ''}`}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((s, i) => (
                    <tr
                      key={s.id}
                      onClick={() => handleSelectSlate(s.id)}
                      className={`cursor-pointer transition-colors hover:bg-surface-overlay ${i % 2 === 0 ? 'bg-surface-raised' : 'bg-surface-base/30'} ${s.id === selectedId ? 'ring-2 ring-primary ring-inset' : ''}`}
                    >
                      <td className={tdCls}>
                        <span className={`text-base ${s.id === selectedId ? 'text-success' : 'text-surface-border'}`}>{'\u25CF'}</span>
                      </td>
                      <td className={tdCls}>
                        <span className="text-text-primary font-semibold capitalize">{s.platform}</span>
                      </td>
                      <td className={tdCls}>
                        <span className="text-[#a78bfa] uppercase text-[11px] font-bold">{s.sport}</span>
                      </td>
                      <td className={tdCls}>{s.date}</td>
                      <td className={tdCls}>{s.slate_type}</td>
                      <td className={`${tdCls} text-right`}>{s.player_count}</td>
                      <td className={`${tdCls} text-right`}><StatusPill status={s.status} /></td>
                      <td className={`${tdCls} text-right text-text-muted whitespace-nowrap`}>
                        {new Date(s.created_at).toLocaleDateString()}{' '}
                        <span className="text-text-muted text-[11px]">
                          {new Date(s.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                        </span>
                      </td>
                      <td className={`${tdCls} text-right`}>
                        <button
                          onClick={e => { e.stopPropagation(); setDeleteTarget(s) }}
                          className="bg-transparent border border-surface-border rounded px-2 py-0.5 text-[11px] text-text-muted cursor-pointer hover:border-danger hover:text-danger transition-colors"
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* ── Selected Slate: Injury + Player Details ───────────────────────── */}
        {selectedId && (
          <div className="mt-5 space-y-4">
            {playersLoading ? (
              <div className="py-8 text-center text-text-muted bg-surface-raised border border-surface-border rounded-xl">
                Loading player data\u2026
              </div>
            ) : (
              <>
                {/* Injury alert banner */}
                <InjuryAlertBanner summary={injurySummary} />

                {/* Injury summary panel (collapsible, grouped by team) */}
                {slatePlayers.length > 0 && (
                  <InjurySummaryPanel
                    players={slatePlayers}
                    summary={injurySummary}
                    onRefresh={handleRefresh}
                    refreshing={refreshing}
                  />
                )}

                {/* Player table */}
                {slatePlayers.length > 0 && (
                  <SlatePlayerTable players={slatePlayers} search={search} />
                )}
              </>
            )}
          </div>
        )}
      </div>

      {showUpload && (
        <UploadModal
          onClose={() => setShowUpload(false)}
          onSuccess={() => { setShowUpload(false); loadSlates(); showToast('Slate uploaded') }}
        />
      )}
      {deleteTarget && (
        <ConfirmModal
          slate={deleteTarget}
          deleting={deleting}
          onConfirm={handleDelete}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  )
}
