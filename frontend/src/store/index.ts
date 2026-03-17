import { useSyncExternalStore } from 'react'

type Toast = { type: 'success' | 'error' | 'info'; title: string; message?: string }

type UIState = {
  currentSlateId: string | null
  addToast: (toast: Toast) => void
  setCurrentSlateId: (id: string) => void
}

let currentSlateId: string | null = null
const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((listener) => listener())
}

export function useUIStore(): UIState {
  const snapshot = useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => currentSlateId,
    () => currentSlateId
  )

  return {
    currentSlateId: snapshot,
    addToast: (toast) => {
      if (toast.type === 'error') {
        console.error(`${toast.title}: ${toast.message ?? ''}`)
      } else {
        console.log(`${toast.title}: ${toast.message ?? ''}`)
      }
    },
    setCurrentSlateId: (id: string) => {
      currentSlateId = id
      emit()
    },
  }
}
