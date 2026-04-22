import { useEffect, useRef, useState } from 'react'
import type { Snapshot } from '../types'

const STORAGE_KEY = 'workcafe_snapshot'

export function useSnapshot() {
  const [snapshot, setSnapshotState] = useState<string>(() => {
    return localStorage.getItem(STORAGE_KEY) || ''
  })

  const setSnapshot = (name: string) => {
    if (name) localStorage.setItem(STORAGE_KEY, name)
    else localStorage.removeItem(STORAGE_KEY)
    setSnapshotState(name)
  }

  // append ?snapshot= to any API URL if a snapshot is selected
  const apiUrl = (url: string) => {
    if (!snapshot) return url
    const sep = url.includes('?') ? '&' : '?'
    return `${url}${sep}snapshot=${encodeURIComponent(snapshot)}`
  }

  return { snapshot, setSnapshot, apiUrl }
}

interface Props {
  snapshot: string
  setSnapshot: (name: string) => void
}

export function SnapshotSelector({ snapshot, setSnapshot }: Props) {
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetch('/api/snapshots')
      .then(r => r.ok ? r.json() : [])
      .then(data => setSnapshots(data ?? []))
      .catch(() => {})
  }, [])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  if (snapshots.length === 0) return null

  const current = snapshots.find(s => s.name === snapshot)
  const label = snapshot ? (current?.name ?? snapshot) : 'Live'
  const isHistorical = !!snapshot

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className={`flex items-center gap-1.5 rounded-lg shadow px-3 py-1.5 text-sm transition-colors ${
          isHistorical
            ? 'bg-amber-50 border border-amber-300 text-amber-800 hover:bg-amber-100'
            : 'bg-white text-gray-700 hover:bg-gray-50'
        }`}
        title={isHistorical ? 'Viewing historical snapshot' : 'Live data'}
      >
        {isHistorical && <span className="text-amber-500">⚠</span>}
        <span className="font-mono text-xs">{label}</span>
        <span className="text-gray-400">▾</span>
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1 z-[700] bg-white rounded-xl shadow-2xl border border-gray-100 w-72 max-h-[60vh] overflow-y-auto">
          <div className="p-2">
            <button
              onClick={() => { setSnapshot(''); setOpen(false) }}
              className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${
                !snapshot ? 'bg-blue-50 text-blue-700 font-medium' : 'hover:bg-gray-50 text-gray-700'
              }`}
            >
              <div className="font-medium">Live</div>
              <div className="text-xs text-gray-400">Current clean.db</div>
            </button>

            {snapshots.length > 0 && (
              <div className="my-2 border-t border-gray-100" />
            )}

            {snapshots.map(s => (
              <button
                key={s.name}
                onClick={() => { setSnapshot(s.name); setOpen(false) }}
                className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${
                  snapshot === s.name ? 'bg-amber-50 text-amber-800 font-medium' : 'hover:bg-gray-50 text-gray-700'
                }`}
              >
                <div className="font-mono text-xs font-medium">{s.name}</div>
                <div className="text-xs text-gray-400 mt-0.5">{s.cafe_count.toLocaleString()} cafes</div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
