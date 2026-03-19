'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { Users, TrendingUp, DollarSign, UserPlus, Search, ChevronLeft, ChevronRight } from 'lucide-react'
import { getStoredUser } from '@/lib/auth'
import { getAdminStats, getAdminUsers, type AdminStats, type AdminUser } from '@/lib/api/admin'
import { useToastStore } from '@/store/toastStore'

// ── Stat card ─────────────────────────────────────────────────────────────
function StatCard({
  label,
  value,
  sub,
  icon: Icon,
  accent,
}: {
  label: string
  value: string | number
  sub?: string
  icon: React.ElementType
  accent: string
}) {
  return (
    <div className="bg-surface-raised border border-surface-border rounded-xl p-5 flex items-start gap-4">
      <div className={`p-2.5 rounded-lg ${accent}`}>
        <Icon className="w-5 h-5" />
      </div>
      <div>
        <p className="text-xs text-text-muted font-medium uppercase tracking-wide">{label}</p>
        <p className="text-2xl font-bold text-text-primary mt-0.5">{value}</p>
        {sub && <p className="text-xs text-text-muted mt-0.5">{sub}</p>}
      </div>
    </div>
  )
}

// ── Tier badge ────────────────────────────────────────────────────────────
const TIER_STYLES: Record<string, string> = {
  pro:   'bg-primary/10 text-primary border border-primary/30',
  elite: 'bg-warning/10 text-warning border border-warning/30',
  free:  'bg-surface-overlay text-text-muted border border-surface-border',
}
function TierBadge({ tier }: { tier: string }) {
  return (
    <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold uppercase tracking-wide ${TIER_STYLES[tier] ?? TIER_STYLES.free}`}>
      {tier}
    </span>
  )
}

const TIERS = ['all', 'free', 'pro', 'elite']

// ── Page ─────────────────────────────────────────────────────────────────
export default function AdminPage() {
  const router = useRouter()
  const { toast } = useToastStore()

  const [stats, setStats] = useState<AdminStats | null>(null)
  const [users, setUsers] = useState<AdminUser[]>([])
  const [total, setTotal] = useState(0)
  const [pages, setPages] = useState(1)
  const [page, setPage] = useState(1)
  const [tierFilter, setTierFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [loading, setLoading] = useState(true)

  // Guard: redirect non-admins immediately
  useEffect(() => {
    const user = getStoredUser()
    if (!user?.is_admin) {
      router.replace('/')
    }
  }, [router])

  // Fetch stats once
  useEffect(() => {
    getAdminStats()
      .then(setStats)
      .catch(() => toast('Failed to load admin stats', 'error'))
  }, [toast])

  // Fetch users on filter/page change
  const fetchUsers = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await getAdminUsers({
        page,
        per_page: 50,
        tier: tierFilter === 'all' ? undefined : tierFilter,
        search: search || undefined,
      })
      setUsers(resp.users)
      setTotal(resp.total)
      setPages(resp.pages)
    } catch {
      toast('Failed to load users', 'error')
    } finally {
      setLoading(false)
    }
  }, [page, tierFilter, search, toast])

  useEffect(() => { fetchUsers() }, [fetchUsers])

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setPage(1)
    setSearch(searchInput)
  }

  const fmtDate = (iso: string | null) =>
    iso ? new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' }) : '—'

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-text-primary">Admin Dashboard</h1>
        <p className="text-sm text-text-muted mt-0.5">Platform metrics and user management</p>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <StatCard
          label="Total Users"
          value={stats?.total_users ?? '—'}
          icon={Users}
          accent="bg-primary/10 text-primary"
        />
        <StatCard
          label="Pro Users"
          value={stats?.pro_users ?? '—'}
          sub={stats ? `${((stats.pro_users / Math.max(stats.total_users, 1)) * 100).toFixed(1)}% conversion` : undefined}
          icon={TrendingUp}
          accent="bg-success/10 text-success"
        />
        <StatCard
          label="Free Users"
          value={stats?.free_users ?? '—'}
          icon={Users}
          accent="bg-surface-overlay text-text-secondary"
        />
        <StatCard
          label="MRR"
          value={stats ? `$${stats.mrr.toLocaleString()}` : '—'}
          sub="pro×$29 + elite×$79"
          icon={DollarSign}
          accent="bg-warning/10 text-warning"
        />
        <StatCard
          label="New (30d)"
          value={stats?.new_users_30d ?? '—'}
          icon={UserPlus}
          accent="bg-primary/10 text-primary"
        />
      </div>

      {/* Users table */}
      <div className="bg-surface-raised border border-surface-border rounded-xl overflow-hidden">
        {/* Table toolbar */}
        <div className="px-5 py-3.5 border-b border-surface-border flex flex-wrap items-center gap-3 justify-between">
          <div className="flex items-center gap-1.5">
            {TIERS.map((t) => (
              <button
                key={t}
                onClick={() => { setTierFilter(t); setPage(1) }}
                className={`px-3 py-1 rounded-full text-xs font-medium capitalize transition-colors ${
                  tierFilter === t
                    ? 'bg-primary text-white'
                    : 'bg-surface-overlay text-text-secondary hover:text-text-primary'
                }`}
              >
                {t}
              </button>
            ))}
          </div>
          <form onSubmit={handleSearchSubmit} className="flex items-center gap-2">
            <input
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search email or name…"
              className="bg-surface-overlay border border-surface-border rounded-md px-3 py-1.5 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-primary/60 w-52"
            />
            <button
              type="submit"
              className="p-1.5 rounded-md bg-surface-overlay border border-surface-border text-text-muted hover:text-text-primary transition-colors"
            >
              <Search className="w-4 h-4" />
            </button>
          </form>
        </div>

        {/* Table */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-surface-border">
                {['Email', 'Name', 'Tier', 'Joined', 'Last Login', 'Lineups', 'Slates'].map((h) => (
                  <th key={h} className="px-4 py-2.5 text-left text-xs font-semibold text-text-muted uppercase tracking-wide">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 8 }).map((_, i) => (
                  <tr key={i} className="border-b border-surface-border/50">
                    {Array.from({ length: 7 }).map((__, j) => (
                      <td key={j} className="px-4 py-3">
                        <div className="skeleton h-4 rounded w-full max-w-[120px]" />
                      </td>
                    ))}
                  </tr>
                ))
              ) : users.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-12 text-center text-text-muted text-sm">
                    No users found.
                  </td>
                </tr>
              ) : (
                users.map((u) => (
                  <tr key={u.id} className="border-b border-surface-border/50 hover:bg-surface-overlay/40 transition-colors">
                    <td className="px-4 py-2.5 text-text-primary font-medium max-w-[220px] truncate">{u.email}</td>
                    <td className="px-4 py-2.5 text-text-secondary">{u.full_name || <span className="text-text-muted italic">—</span>}</td>
                    <td className="px-4 py-2.5"><TierBadge tier={u.tier} /></td>
                    <td className="px-4 py-2.5 text-text-secondary whitespace-nowrap">{fmtDate(u.created_at)}</td>
                    <td className="px-4 py-2.5 text-text-secondary whitespace-nowrap">{fmtDate(u.last_login_at)}</td>
                    <td className="px-4 py-2.5 text-text-secondary text-right">{u.lineups_generated.toLocaleString()}</td>
                    <td className="px-4 py-2.5 text-text-secondary text-right">{u.slates_processed.toLocaleString()}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {pages > 1 && (
          <div className="px-5 py-3 border-t border-surface-border flex items-center justify-between">
            <span className="text-xs text-text-muted">
              {total} user{total !== 1 ? 's' : ''} total
            </span>
            <div className="flex items-center gap-1">
              <button
                disabled={page === 1}
                onClick={() => setPage((p) => p - 1)}
                className="p-1.5 rounded-md text-text-muted hover:text-text-primary hover:bg-surface-overlay disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="text-xs text-text-secondary px-2">
                {page} / {pages}
              </span>
              <button
                disabled={page === pages}
                onClick={() => setPage((p) => p + 1)}
                className="p-1.5 rounded-md text-text-muted hover:text-text-primary hover:bg-surface-overlay disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
