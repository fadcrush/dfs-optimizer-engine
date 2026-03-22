'use client'

import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import { cn } from '@/lib/utils'
import { PageContainer } from '@/components/layout/PageContainer'
import { Card, CardHeader } from '@/components/ui/Card'
import { getAnalyticsRoi, getAnalyticsAccuracy, postContestResult,
  getOwnershipAccuracy, getOwnershipModelStatus, trainOwnershipModel, importOwnershipActuals,
  seedOwnershipFromSlate,
  type RoiSummary, type AccuracyReport, type ContestEntryPayload,
  type OwnershipAccuracyReport, type OwnershipModelStatus } from '@/lib/api'

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function pct(n: number) {
  return `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`
}

function dollar(n: number) {
  const abs = Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return `${n < 0 ? '-' : '+'}$${abs}`
}

// ─────────────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────────────

function StatBox({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-surface-overlay border border-surface-border rounded-lg px-5 py-3.5 flex flex-col items-center min-w-[130px]">
      <span className="text-[11px] text-text-muted uppercase font-semibold tracking-wide">{label}</span>
      <span className={cn('text-2xl font-bold mt-1', color && 'text-[var(--stat-c)]')} style={color ? {'--stat-c': color} as CSSProperties : undefined}>{value}</span>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Page
// ─────────────────────────────────────────────────────────────────────────────

const today = () => new Date().toISOString().slice(0, 10)

const emptyForm = (): ContestEntryPayload => ({
  contest_date: today(),
  contest_type: 'gpp',
  site: 'DK',
  entry_fee: 0,
  payout: 0,
  final_rank: undefined,
  total_entries: undefined,
  notes: '',
})

export default function AnalyticsPage() {
  const [days, setDays] = useState(30)
  const [site, setSite] = useState<'DK' | 'FD'>('DK')
  const [roi, setRoi] = useState<RoiSummary | null>(null)
  const [accuracy, setAccuracy] = useState<AccuracyReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // Contest entry form
  const [form, setForm] = useState<ContestEntryPayload>(emptyForm())
  const [formSaving, setFormSaving] = useState(false)
  const [formMsg, setFormMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [showForm, setShowForm] = useState(false)

  // Ownership accuracy
  const [ownAccuracy, setOwnAccuracy] = useState<OwnershipAccuracyReport | null>(null)
  const [modelStatus, setModelStatus] = useState<OwnershipModelStatus | null>(null)
  const [ownImportMsg, setOwnImportMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [training, setTraining] = useState(false)
  const ownFileRef = useRef<HTMLInputElement>(null)
  const slateFileRef = useRef<HTMLInputElement>(null)

  const setField = <K extends keyof ContestEntryPayload>(key: K, val: ContestEntryPayload[K]) =>
    setForm(f => ({ ...f, [key]: val }))

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    const [roiRes, accRes, ownRes, statusRes] = await Promise.all([
      getAnalyticsRoi(days, site),
      getAnalyticsAccuracy(days, site),
      getOwnershipAccuracy(days, site),
      getOwnershipModelStatus(),
    ])
    setLoading(false)
    if (!roiRes.success) { setError(roiRes.error ?? 'Failed to load ROI'); return }
    if (!accRes.success) { setError(accRes.error ?? 'Failed to load accuracy'); return }
    setRoi(roiRes.data ?? null)
    setAccuracy(accRes.data ?? null)
    if (ownRes.success) setOwnAccuracy(ownRes.data ?? null)
    if (statusRes.success) setModelStatus(statusRes.data ?? null)
  }, [days, site])

  const submitContest = async () => {
    if (form.entry_fee <= 0) {
      setFormMsg({ ok: false, text: 'Entry fee must be greater than 0.' })
      return
    }
    setFormSaving(true)
    setFormMsg(null)
    const res = await postContestResult(form)
    setFormSaving(false)
    if (res.success) {
      setFormMsg({ ok: true, text: 'Contest entry logged!' })
      setForm(emptyForm())
      load()
    } else {
      setFormMsg({ ok: false, text: res.error ?? 'Failed to log contest.' })
    }
  }

  useEffect(() => { load() }, [load])

  return (
    <PageContainer
      title="Analytics"
      description="Contest ROI tracking & projection accuracy metrics."
    >
      <div className="max-w-[1100px] mx-auto">

        {/* Controls */}
        <div className="flex gap-3 mb-6 flex-wrap items-center">
          <div>
            <label className={labelCls}>Site</label>
            <select value={site} onChange={e => setSite(e.target.value as 'DK' | 'FD')} className={selectCls}>
              <option value="DK">DraftKings</option>
              <option value="FD">FanDuel</option>
            </select>
          </div>
          <div>
            <label className={labelCls}>Lookback</label>
            <select value={days} onChange={e => setDays(Number(e.target.value))} className={selectCls}>
              {[7, 14, 30, 60, 90].map(d => (
                <option key={d} value={d}>{d} days</option>
              ))}
            </select>
          </div>
          <button onClick={load} className={cn(btnCls, 'self-end')}>
            Refresh
          </button>
        </div>

        {error && (
          <div className="bg-danger-muted border border-danger/50 rounded-lg px-4 py-3 text-danger text-sm mb-5">{error}</div>
        )}

        {loading && (
          <p className="text-text-muted">Loading...</p>
        )}

        {/* Log Contest Entry */}
        <Card className="mb-6">
          <div className="p-4">
            <div className={`flex items-center justify-between${showForm ? ' mb-4' : ''}`}>
              <span className="font-bold text-[15px] text-text-primary">Log Contest Entry</span>
              <button
                onClick={() => { setShowForm(s => !s); setFormMsg(null) }}
                className={cn(btnCls, 'text-xs py-1 px-3')}
              >
                {showForm ? 'Hide' : '+ New Entry'}
              </button>
            </div>

            {showForm && (
              <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(180px,1fr))]">
                {/* Date */}
                <div>
                  <label className={labelCls}>Date</label>
                  <input type="date" value={form.contest_date}
                    onChange={e => setField('contest_date', e.target.value)}
                    className={cn(selectCls, 'w-full')} />
                </div>
                {/* Site */}
                <div>
                  <label className={labelCls}>Site</label>
                  <select value={form.site} onChange={e => setField('site', e.target.value as 'DK' | 'FD')}
                    className={cn(selectCls, 'w-full')}>
                    <option value="DK">DraftKings</option>
                    <option value="FD">FanDuel</option>
                  </select>
                </div>
                {/* Contest type */}
                <div>
                  <label className={labelCls}>Type</label>
                  <select value={form.contest_type}
                    onChange={e => setField('contest_type', e.target.value as ContestEntryPayload['contest_type'])}
                    className={cn(selectCls, 'w-full')}>
                    <option value="gpp">GPP</option>
                    <option value="double_up">Double Up</option>
                    <option value="cash">Cash</option>
                    <option value="winner_take_all">Winner Take All</option>
                  </select>
                </div>
                {/* Entry fee */}
                <div>
                  <label className={labelCls}>Entry Fee ($)</label>
                  <input type="number" min="0" step="0.25" value={form.entry_fee}
                    onChange={e => setField('entry_fee', parseFloat(e.target.value) || 0)}
                    className={cn(selectCls, 'w-full')} />
                </div>
                {/* Payout */}
                <div>
                  <label className={labelCls}>Payout ($)</label>
                  <input type="number" min="0" step="0.01" value={form.payout ?? 0}
                    onChange={e => setField('payout', parseFloat(e.target.value) || 0)}
                    className={cn(selectCls, 'w-full')} />
                </div>
                {/* Final Rank */}
                <div>
                  <label className={labelCls}>Final Rank</label>
                  <input type="number" min="1" placeholder="optional"
                    value={form.final_rank ?? ''}
                    onChange={e => setField('final_rank', e.target.value ? parseInt(e.target.value) : undefined)}
                    className={cn(selectCls, 'w-full')} />
                </div>
                {/* Total entries */}
                <div>
                  <label className={labelCls}>Total Entries</label>
                  <input type="number" min="1" placeholder="optional"
                    value={form.total_entries ?? ''}
                    onChange={e => setField('total_entries', e.target.value ? parseInt(e.target.value) : undefined)}
                    className={cn(selectCls, 'w-full')} />
                </div>
                {/* Notes */}
                <div className="col-span-2">
                  <label className={labelCls}>Notes</label>
                  <input type="text" placeholder="optional" value={form.notes ?? ''}
                    onChange={e => setField('notes', e.target.value)}
                    className={cn(selectCls, 'w-full')} />
                </div>
                {/* Submit row */}
                <div className="col-span-full flex items-center gap-3 mt-1">
                  <button
                    onClick={submitContest}
                    disabled={formSaving}
                    className={cn(btnCls, 'bg-primary border-primary/50 text-white disabled:opacity-60')}
                  >
                    {formSaving ? 'Saving…' : 'Log Entry'}
                  </button>
                  {formMsg && (
                    <span className={cn('text-sm', formMsg.ok ? 'text-success' : 'text-danger')}>
                      {formMsg.text}
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>
        </Card>

        {/* ROI Summary */}
        {roi && !loading && (
          <Card className="mb-6">
            <CardHeader title={`ROI Summary — Last ${days} Days (${site})`} />
            <div className="p-4">
              <div className="flex gap-3 flex-wrap mb-5">
                <StatBox label="Contests" value={String(roi.total_contests)} />
                <StatBox label="Invested" value={`$${roi.total_invested.toLocaleString()}`} />
                <StatBox label="Won" value={`$${roi.total_won.toLocaleString()}`} />
                <StatBox
                  label="Profit"
                  value={dollar(roi.profit)}
                  color={roi.profit >= 0 ? '#22c55e' : '#ef4444'}
                />
                <StatBox
                  label="ROI"
                  value={pct(roi.roi_percentage)}
                  color={roi.roi_percentage >= 0 ? '#22c55e' : '#ef4444'}
                />
              </div>

              {/* By contest type */}
              {Object.keys(roi.roi_by_type).length > 0 && (
                <div>
                  <p className="text-[11px] text-text-muted uppercase font-semibold tracking-wide mt-0 mb-2">By Contest Type</p>
                  <table className="w-full border-collapse text-sm">
                    <thead>
                      <tr>
                        {['Type', 'Entries', 'Invested', 'Won', 'Profit', 'ROI'].map(h => (
                          <th key={h} className={thCls}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(roi.roi_by_type).map(([type, d]) => (
                        <tr key={type}>
                          <td className={tdCls}>{type.toUpperCase()}</td>
                          <td className={tdCls}>{d.count}</td>
                          <td className={tdCls}>${d.invested.toLocaleString()}</td>
                          <td className={tdCls}>${d.won.toLocaleString()}</td>
                          <td className={cn(tdCls, d.profit >= 0 ? 'text-success' : 'text-danger')}>
                            {dollar(d.profit)}
                          </td>
                          <td className={cn(tdCls, 'font-semibold', d.roi >= 0 ? 'text-success' : 'text-danger')}>
                            {pct(d.roi)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {roi.total_contests === 0 && (
                <p className="text-text-muted text-sm">
                  No contest results logged yet. Use <code>POST /analytics/contest</code> to add entries.
                </p>
              )}
            </div>
          </Card>
        )}

        {/* Projection Accuracy */}
        {accuracy && !loading && (
          <Card className="mb-6">
            <CardHeader title={`Projection Accuracy — Last ${days} Days (${site})`} />
            <div className="p-4">
              {accuracy.total_projections === 0 ? (
                <p className="text-text-muted text-sm">
                  No reconciled projections yet. Accuracy data populates after slates when game-log
                  actuals are available. Run <code>POST /analytics/reconcile</code> post-slate.
                </p>
              ) : (
                <>
                  <div className="flex gap-3 flex-wrap mb-5">
                    <StatBox label="Projections" value={String(accuracy.total_projections)} />
                    <StatBox
                      label="MAE"
                      value={accuracy.mae !== null ? `${accuracy.mae.toFixed(2)} pts` : '—'}
                      color="#60a5fa"
                    />
                    <StatBox
                      label="RMSE"
                      value={accuracy.rmse !== null ? `${accuracy.rmse.toFixed(2)} pts` : '—'}
                      color="#a78bfa"
                    />
                    <StatBox
                      label="Bias"
                      value={accuracy.bias !== null ? `${accuracy.bias > 0 ? '+' : ''}${accuracy.bias.toFixed(2)}` : '—'}
                      color={accuracy.bias !== null && accuracy.bias > 0.5 ? '#f97316' : '#9ca3af'}
                    />
                  </div>

                  {accuracy.by_day.length > 0 && (
                    <div>
                      <p className="text-[11px] text-text-muted uppercase font-semibold tracking-wide mt-0 mb-2">Daily Breakdown</p>
                      <table className="w-full border-collapse text-sm">
                        <thead>
                          <tr>
                            {['Date', 'Players', 'MAE', 'RMSE', 'Bias'].map(h => (
                              <th key={h} className={thCls}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {accuracy.by_day.map(d => (
                            <tr key={d.date}>
                              <td className={tdCls}>{d.date}</td>
                              <td className={tdCls}>{d.count}</td>
                              <td className={cn(tdCls, 'text-primary')}>{d.mae.toFixed(2)}</td>
                              <td className={cn(tdCls, 'text-purple-400')}>{d.rmse.toFixed(2)}</td>
                              <td className={cn(tdCls, d.bias > 0.5 ? 'text-orange-400' : 'text-text-muted')}>
                                {d.bias > 0 ? '+' : ''}{d.bias.toFixed(2)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}
            </div>
          </Card>
        )}

        {/* Ownership Accuracy */}
        <Card>
          <CardHeader title={`Ownership Accuracy — Last ${days} Days (${site})`} />
          <div className="p-4">

            {/* Model status badges */}
            {modelStatus && (
              <div className="flex gap-2.5 flex-wrap mb-4">
                {(['DK', 'FD'] as const).map(s => {
                  const m = modelStatus.models[s]
                  const rows = modelStatus.training_rows[s]
                  const ready = modelStatus.ready[s]
                  return (
                    <div
                      key={s}
                      className={cn(
                        'px-3.5 py-1.5 rounded text-xs',
                        ready
                          ? 'bg-success-muted border border-success/40 text-success'
                          : 'bg-surface-overlay border border-surface-border text-text-secondary',
                      )}
                    >
                      <strong>{s}</strong>: {rows.toLocaleString()} rows
                      {m.trained_at && <span className="ml-1.5 opacity-70">· retrained {m.age_days}d ago</span>}
                      {!ready && <span className="ml-1.5 text-orange-400">· needs {modelStatus.min_rows_required - rows} more rows</span>}
                    </div>
                  )
                })}
              </div>
            )}

            {/* Accuracy stat boxes */}
            {ownAccuracy && ownAccuracy.total_players > 0 ? (
              <>
                <div className="flex gap-3 flex-wrap mb-5">
                  <StatBox label="Players" value={String(ownAccuracy.total_players)} />
                  <StatBox
                    label="MAE"
                    value={ownAccuracy.mae !== null ? `${ownAccuracy.mae.toFixed(1)}pp` : '—'}
                    color="#60a5fa"
                  />
                  <StatBox
                    label="Bias"
                    value={ownAccuracy.bias !== null ? `${ownAccuracy.bias > 0 ? '+' : ''}${ownAccuracy.bias.toFixed(1)}pp` : '—'}
                    color={ownAccuracy.bias !== null && Math.abs(ownAccuracy.bias) > 2 ? '#f97316' : '#9ca3af'}
                  />
                  <StatBox
                    label="Chalk Acc"
                    value={ownAccuracy.chalk_accuracy !== null ? `${ownAccuracy.chalk_accuracy.toFixed(0)}%` : '—'}
                    color="#a78bfa"
                  />
                  <StatBox
                    label="Model %"
                    value={ownAccuracy.model_pct !== null ? `${ownAccuracy.model_pct.toFixed(0)}%` : '—'}
                    color={ownAccuracy.model_pct !== null && ownAccuracy.model_pct > 50 ? '#4ade80' : '#9ca3af'}
                  />
                </div>
                {ownAccuracy.note && (
                  <p className="text-[12px] text-text-muted mb-3">{ownAccuracy.note}</p>
                )}
                {ownAccuracy.by_day.length > 0 && (
                  <div>
                    <p className="text-[11px] text-text-muted uppercase font-semibold tracking-wide mt-0 mb-2">Daily Breakdown</p>
                    <table className="w-full border-collapse text-sm">
                      <thead>
                        <tr>
                          {['Date', 'Players', 'MAE', 'Bias'].map(h => (
                            <th key={h} className={thCls}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {ownAccuracy.by_day.map(d => (
                          <tr key={d.date}>
                            <td className={tdCls}>{d.date}</td>
                            <td className={tdCls}>{d.count}</td>
                            <td className={cn(tdCls, 'text-primary')}>{d.mae.toFixed(1)}pp</td>
                            <td className={cn(tdCls, Math.abs(d.bias) > 2 ? 'text-orange-400' : 'text-text-muted')}>
                              {d.bias > 0 ? '+' : ''}{d.bias.toFixed(1)}pp
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            ) : (
              <p className="text-text-muted text-sm mb-4">
                No ownership actuals imported yet. Upload a DraftKings or FanDuel contest CSV
                below to start calibrating the GBR ownership model.
              </p>
            )}

            {/* Import + Train actions */}
            <div className="flex gap-2.5 flex-wrap items-center mt-5 pt-4 border-t border-surface-border">
              {/* Hidden file inputs */}
              <input ref={ownFileRef} type="file" accept=".csv" className="hidden"
                onChange={async e => {
                  const f = e.target.files?.[0]
                  if (!f) return
                  setOwnImportMsg(null)
                  const gameDate = new Date().toISOString().slice(0, 10)
                  const res = await importOwnershipActuals(f, site, gameDate)
                  if (res.success && res.data) {
                    setOwnImportMsg({ ok: true, text: `Imported ${res.data.imported} rows (skipped ${res.data.skipped})` })
                    load()
                  } else {
                    setOwnImportMsg({ ok: false, text: res.error ?? 'Import failed' })
                  }
                  if (ownFileRef.current) ownFileRef.current.value = ''
                }} />
              <input ref={slateFileRef} type="file" accept=".csv" className="hidden"
                onChange={async e => {
                  const f = e.target.files?.[0]
                  if (!f) return
                  setOwnImportMsg(null)
                  const gameDate = new Date().toISOString().slice(0, 10)
                  const res = await seedOwnershipFromSlate(f, site, gameDate)
                  if (res.success && res.data) {
                    const top = res.data.top_players.map(p => `${p.name} ~${p.est_own}%`).join(', ')
                    setOwnImportMsg({ ok: true, text: `Seeded ${res.data.seeded} rows from salary file. Top: ${top}` })
                    load()
                  } else {
                    setOwnImportMsg({ ok: false, text: res.error ?? 'Seed failed' })
                  }
                  if (slateFileRef.current) slateFileRef.current.value = ''
                }} />
              {/* Buttons */}
              <button
                onClick={() => slateFileRef.current?.click()}
                className={cn(btnCls, 'bg-primary-muted border-primary/50 text-primary hover:border-primary/80')}
                title="Seed training data from a DraftKings/FanDuel salary CSV using FPPG-derived ownership estimates"
              >
                Seed from Salary CSV
              </button>
              <button
                onClick={() => ownFileRef.current?.click()}
                className={btnCls}
                title="Import real ownership actuals from a post-contest DK/FD export CSV"
              >
                Import Contest CSV
              </button>
              <button
                onClick={async () => {
                  setTraining(true)
                  setOwnImportMsg(null)
                  const res = await trainOwnershipModel(site)
                  setTraining(false)
                  if (res.success && res.data) {
                    setOwnImportMsg({ ok: res.data.success, text: res.data.message })
                    if (res.data.success) load()
                  } else {
                    setOwnImportMsg({ ok: false, text: res.error ?? 'Training failed' })
                  }
                }}
                disabled={training}
                className={cn(btnCls, 'bg-primary border-primary/70 text-white disabled:opacity-60')}
              >
                {training ? 'Training…' : 'Re-train Model'}
              </button>
              {ownImportMsg && (
                <span className={cn('text-sm', ownImportMsg.ok ? 'text-success' : 'text-danger')}>
                  {ownImportMsg.text}
                </span>
              )}
            </div>
          </div>
        </Card>

      </div>
    </PageContainer>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Tailwind class constants (module-scope, no CSSProperties needed)
// ─────────────────────────────────────────────────────────────────────────────

const labelCls = 'block text-[11px] text-text-muted uppercase mb-1 font-semibold'
const selectCls = 'bg-surface-overlay text-text-primary border border-surface-border rounded px-2.5 py-1.5 text-sm cursor-pointer outline-none focus:border-primary'
const btnCls    = 'bg-surface-overlay text-text-secondary border border-surface-border rounded px-4 py-1.5 text-sm cursor-pointer hover:text-text-primary hover:border-surface-border/80 transition-colors'
const thCls     = 'px-2.5 py-2 text-left border-b border-surface-border text-[11px] text-text-muted font-semibold uppercase'
const tdCls     = 'px-2.5 py-2 text-text-secondary border-b border-surface-border/50 text-sm'
