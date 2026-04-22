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

  const apiUrl = (url: string) => {
    if (!snapshot) return url
    const sep = url.includes('?') ? '&' : '?'
    return `${url}${sep}snapshot=${encodeURIComponent(snapshot)}`
  }

  return { snapshot, setSnapshot, apiUrl }
}

function renderMd(md: string): string {
  return md
    .replace(/^#+ .+$/gm, '')                  // drop all headings
    .replace(/<!--[\s\S]*?-->/g, '')            // drop comments
    .replace(/```([\s\S]*?)```/g, (_, inner) =>
      `<pre class="bg-gray-100 rounded p-2 text-xs font-mono overflow-x-auto my-2 whitespace-pre">${inner.trim()}</pre>`)
    .replace(/\*\*(.+?)\*\*/g, '<strong class="text-gray-700">$1</strong>')
    .replace(/\n{2,}/g, '<br/>')
    .replace(/\n/g, ' ')
    .trim()
}

interface Props {
  snapshot: string
  setSnapshot: (name: string) => void
}

export function SnapshotSelector({ snapshot, setSnapshot }: Props) {
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [open, setOpen] = useState(false)
  const modalRef = useRef<HTMLDivElement>(null)
  const isHistorical = !!snapshot

  useEffect(() => {
    fetch('/api/snapshots')
      .then(r => r.ok ? r.json() : [])
      .then(data => setSnapshots(data ?? []))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open])

  const handleBackdrop = (e: React.MouseEvent) => {
    if (modalRef.current && !modalRef.current.contains(e.target as Node)) setOpen(false)
  }

  const use = (name: string) => { setSnapshot(name); setOpen(false) }

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className={`flex items-center gap-1.5 rounded-lg shadow px-3 py-1.5 text-sm transition-colors ${
          isHistorical
            ? 'bg-amber-50 border border-amber-300 text-amber-800 hover:bg-amber-100'
            : 'bg-white text-gray-700 hover:bg-gray-50'
        }`}
        title="DB History"
      >
        {isHistorical && <span className="text-amber-500">⚠</span>}
        <span className="font-mono text-xs">{snapshot || 'Live'}</span>
        <span className="text-gray-400">🕐</span>
      </button>

      {open && (
        <div
          className="fixed inset-0 z-[800] flex items-center justify-center bg-black/40"
          onMouseDown={handleBackdrop}
        >
          <div
            ref={modalRef}
            className="bg-white rounded-2xl shadow-2xl w-[680px] max-w-[95vw] max-h-[80vh] flex flex-col"
          >
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
              <h2 className="text-base font-semibold text-gray-900">DB History</h2>
              <button onClick={() => setOpen(false)} className="text-gray-400 hover:text-gray-600 text-lg leading-none">✕</button>
            </div>

            {/* List */}
            <div className="overflow-y-auto flex-1 p-4 flex flex-col gap-3">
              {/* Live row */}
              <div className={`rounded-xl border p-4 ${!snapshot ? 'border-blue-300 bg-blue-50' : 'border-gray-200'}`}>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-semibold text-sm text-gray-900">Live</div>
                    <div className="text-xs text-gray-500 mt-0.5">Current clean.db — live data</div>
                  </div>
                  {snapshot ? (
                    <button
                      onClick={() => use('')}
                      className="shrink-0 px-3 py-1 text-xs rounded-lg bg-blue-600 text-white hover:bg-blue-700 font-medium"
                    >
                      Use
                    </button>
                  ) : (
                    <span className="shrink-0 px-3 py-1 text-xs rounded-lg bg-blue-100 text-blue-700 font-medium">Active</span>
                  )}
                </div>
              </div>

              {snapshots.length === 0 && (
                <div className="text-sm text-gray-400 text-center py-8">No snapshots yet</div>
              )}

              {snapshots.map(s => {
                const isActive = snapshot === s.name
                return (
                  <div
                    key={s.name}
                    className={`rounded-xl border p-4 ${isActive ? 'border-amber-300 bg-amber-50' : 'border-gray-200'}`}
                  >
                    <div className="flex items-start justify-between gap-3 mb-2">
                      <div>
                        <div className="font-mono text-sm font-semibold text-gray-900">{s.name}</div>
                        <div className="text-xs text-gray-500 mt-0.5">{s.cafe_count.toLocaleString()} cafes · {s.date}</div>
                      </div>
                      {isActive ? (
                        <span className="shrink-0 px-3 py-1 text-xs rounded-lg bg-amber-100 text-amber-700 font-medium">Active</span>
                      ) : (
                        <button
                          onClick={() => use(s.name)}
                          className="shrink-0 px-3 py-1 text-xs rounded-lg bg-gray-800 text-white hover:bg-gray-700 font-medium"
                        >
                          Use
                        </button>
                      )}
                    </div>
                    {s.notes && (
                      <div
                        className="text-xs text-gray-600 border-t border-gray-100 pt-2 mt-1"
                        dangerouslySetInnerHTML={{ __html: renderMd(s.notes) }}
                      />
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
