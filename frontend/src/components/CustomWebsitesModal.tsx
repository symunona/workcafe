import { useEffect, useState } from 'react'
import { useSnapshot } from './SnapshotSelector'

// module-level cache keyed by snapshot name ('' = live)
const cache = new Map<string, CafeWithSite[]>()

interface CafeWithSite {
  id: string
  name: string
  english_name?: string
  lat: number
  lon: number
  address: string
  website_url: string
  image_count: number
}

interface Props {
  onClose: () => void
  onSelectCafe?: (id: string) => void
}

export function CustomWebsitesModal({ onClose, onSelectCafe }: Props) {
  const { snapshot, apiUrl } = useSnapshot()
  const [cafes, setCafes] = useState<CafeWithSite[]>(() => cache.get(snapshot) ?? [])
  const [loading, setLoading] = useState(!cache.has(snapshot))
  const [search, setSearch] = useState('')

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [onClose])

  useEffect(() => {
    if (cache.has(snapshot)) return
    setLoading(true)
    fetch(apiUrl('/api/custom-websites'))
      .then(r => r.json())
      .then(data => {
        const list = data.cafes ?? []
        cache.set(snapshot, list)
        setCafes(list)
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [snapshot])

  const filtered = cafes.filter(c => {
    if (!search) return true
    const q = search.toLowerCase()
    return c.name.toLowerCase().includes(q) ||
      (c.english_name ?? '').toLowerCase().includes(q) ||
      c.website_url.toLowerCase().includes(q)
  })

  return (
    <div className="fixed inset-0 z-[1000] flex items-start justify-center pt-16 px-4 pb-4">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[80vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b shrink-0">
          <div>
            <h2 className="text-lg font-semibold">Custom Websites</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Independent cafes with their own website — not Instagram, not chains
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-xl font-light leading-none">×</button>
        </div>

        {/* Search */}
        <div className="px-6 py-3 border-b shrink-0">
          <input
            type="text"
            placeholder="Search cafes or URLs…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
            autoFocus
          />
        </div>

        {/* Count */}
        <div className="px-6 py-2 text-xs text-gray-400 border-b shrink-0">
          {loading ? 'Loading…' : `${filtered.length.toLocaleString()} of ${cafes.length.toLocaleString()} cafes`}
        </div>

        {/* List */}
        <div className="overflow-y-auto flex-1">
          {loading ? (
            <div className="flex items-center justify-center h-32 text-gray-400 text-sm">Loading…</div>
          ) : filtered.length === 0 ? (
            <div className="flex items-center justify-center h-32 text-gray-400 text-sm">No results</div>
          ) : (
            <ul className="divide-y">
              {filtered.map(c => (
                <li key={c.id} className="px-6 py-3 hover:bg-gray-50 flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <button
                      onClick={() => { onSelectCafe?.(c.id); onClose() }}
                      className="font-medium text-sm text-gray-800 hover:text-blue-600 text-left truncate block"
                    >
                      {c.english_name || c.name}
                      {c.english_name && c.name !== c.english_name && (
                        <span className="ml-1.5 text-xs text-gray-400 font-normal">{c.name}</span>
                      )}
                    </button>
                    <div className="text-xs text-gray-400 mt-0.5 truncate">{c.address}</div>
                  </div>
                  <a
                    href={c.website_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-blue-500 hover:text-blue-700 hover:underline shrink-0 max-w-[200px] truncate block"
                    title={c.website_url}
                  >
                    {c.website_url.replace(/^https?:\/\//, '').replace(/\/$/, '')}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
