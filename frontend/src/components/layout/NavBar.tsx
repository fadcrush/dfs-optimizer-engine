'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'

const NAV_ITEMS = [
  { href: '/',             label: 'Home'        },
  { href: '/slates',       label: 'Slates'      },
  { href: '/projections',  label: 'Projections' },
  { href: '/optimizer',    label: 'Optimizer'   },
  { href: '/ev-modeling',  label: 'EV Modeling' },
  { href: '/simulation',   label: 'Simulation'  },
  { href: '/late-swap',    label: 'Late Swap'   },
  { href: '/analytics',    label: 'Analytics'   },
  { href: '/metrics',      label: 'Metrics'     },
  { href: '/auth',         label: 'Auth'        },
]

function useClock() {
  const [display, setDisplay] = useState('')
  useEffect(() => {
    const fmt = () => {
      const now = new Date()
      const day = now.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
      const time = now.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
      setDisplay(`${day}  ${time}`)
    }
    fmt()
    const id = setInterval(fmt, 30_000)
    return () => clearInterval(id)
  }, [])
  return display
}

export function NavBar() {
  const path = usePathname()
  const clock = useClock()

  return (
    <nav className="bg-[#1e2d40] border-b border-[#334155] px-5 flex items-center h-12 sticky top-0 z-[100]">
      {/* Brand */}
      <span className="font-extrabold text-sm text-[#3b82f6] tracking-[-0.3px] mr-5 whitespace-nowrap">
        DFS Edge Pro
      </span>

      {/* Nav links */}
      <div className="flex gap-0.5 flex-1 overflow-x-auto">
        {NAV_ITEMS.map(({ href, label }) => {
          const active = path === href || (href !== '/' && path.startsWith(href))
          return (
            <Link
              key={href}
              href={href}
              className={`px-3 py-[5px] rounded-[5px] text-xs no-underline whitespace-nowrap transition-[background,color] duration-150 ${active ? 'font-semibold text-white bg-[#2563eb]' : 'font-medium text-[#cbd5e1] bg-transparent'}`}
            >
              {label}
            </Link>
          )
        })}
      </div>

      {/* Right: clock + user */}
      <div className="flex items-center gap-4 ml-4">
        {clock && (
          <span className="text-[11px] text-text-muted whitespace-nowrap">
            {clock}
          </span>
        )}
        <div className="w-7 h-7 rounded-full bg-[#3b82f6] flex items-center justify-center text-[11px] font-bold text-white shrink-0">
          D
        </div>
      </div>
    </nav>
  )
}
