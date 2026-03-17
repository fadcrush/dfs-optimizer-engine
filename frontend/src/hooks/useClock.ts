'use client'
import { useEffect, useState } from 'react'

export function useClock() {
  const [display, setDisplay] = useState('')

  useEffect(() => {
    const fmt = () => {
      const now = new Date()
      const day  = now.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
      const time = now.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
      setDisplay(`${day}  ${time}`)
    }
    fmt()
    const id = setInterval(fmt, 30_000)
    return () => clearInterval(id)
  }, [])

  return display
}
