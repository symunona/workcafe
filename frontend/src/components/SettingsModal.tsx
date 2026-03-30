import { useEffect, useState, useCallback } from 'react'
import { CloseIcon } from './Icons'

interface ServiceStatus {
  name: string
  unit: string
  state: string
  active: boolean
}

interface ProviderMetrics {
  provider: string
  cafes_last_hour: number
  cafes_24h: number
  images_last_hour: number
  images_24h: number
  total: number
}

interface DiskStats {
  data_dir_gb: number
  limit_gb: number
  used_pct: number
}

interface QueueEntry {
  queue_depth: number
  updated_at: string
}

interface StatusData {
  services: ServiceStatus[]
  per_provider: ProviderMetrics[]
  total_cafes: number
  total_images: number
  cafes_last_hour: number
  cafes_24h: number
  images_last_hour: number
  images_24h: number
  last_cafe_at: string
  last_image_at: string
  disk: DiskStats
  db_queue: Record<string, QueueEntry>
}

const SERVICE_LABELS: Record<string, string> = {
  kakao: 'Kakao Scraper',
  google: 'Google Scraper',
  osm: 'OSM Scraper',
  naver: 'Naver Scraper',
  imagescraper: 'Image Scraper (Kakao)',
  api: 'API Server',
  frontend: 'Frontend',
}

function timeSince(iso: string): string {
  if (!iso) return 'never'
  const diff = Date.now() - new Date(iso.replace(' ', 'T') + (iso.includes('Z') ? '' : 'Z')).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function healthColor(lastAt: string, activeServices: boolean): string {
  if (!lastAt) return '#ef4444'
  const diffH = (Date.now() - new Date(lastAt.replace(' ', 'T') + (lastAt.includes('Z') ? '' : 'Z')).getTime()) / 3600000
  if (!activeServices) return '#f97316'
  if (diffH < 1) return '#22c55e'
  if (diffH < 4) return '#eab308'
  return '#ef4444'
}

interface SettingsModalProps {
  onClose: () => void
}

export function SettingsModal({ onClose }: SettingsModalProps) {
  const [status, setStatus] = useState<StatusData | null>(null)
  const [loading, setLoading] = useState(true)
  const [toggling, setToggling] = useState<Record<string, boolean>>({})

  const fetchStatus = useCallback(() => {
    fetch('/api/status')
      .then(r => r.json())
      .then((d: StatusData) => { setStatus(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  useEffect(() => {
    fetchStatus()
    const id = setInterval(fetchStatus, 10000)
    return () => clearInterval(id)
  }, [fetchStatus])

  async function toggle(name: string, currentlyActive: boolean) {
    const action = currentlyActive ? 'stop' : 'start'
    setToggling(t => ({ ...t, [name]: true }))
    try {
      await fetch(`/api/services/${name}/${action}`, { method: 'POST' })
      await new Promise(r => setTimeout(r, 1000))
      fetchStatus()
    } finally {
      setToggling(t => ({ ...t, [name]: false }))
    }
  }

  const scraperActive = status?.services.some(s => (s.name === 'datascraper' || s.name === 'imagescraper') && s.active) ?? false

  return (
    <div className="fixed inset-0 z-[2000] bg-black/60 flex items-center justify-center sm:p-4 backdrop-blur-sm animate-in fade-in">
      <div className="bg-white sm:rounded-2xl shadow-2xl w-full h-full sm:h-auto max-w-3xl sm:max-h-[90vh] overflow-y-auto flex flex-col relative">
        <div className="sticky top-0 bg-white/90 backdrop-blur-md px-6 py-5 border-b border-gray-100 flex items-center justify-between z-10">
          <h2 className="text-xl font-bold text-gray-900">Scraper Settings &amp; Health</h2>
          <button
            onClick={onClose}
            className="w-10 h-10 rounded-full bg-gray-100 flex items-center justify-center text-gray-500 hover:bg-gray-200 transition-colors"
          >
            <CloseIcon />
          </button>
        </div>

        {loading ? (
          <div className="p-10 text-center text-gray-400">Loading status…</div>
        ) : !status ? (
          <div className="p-10 text-center text-red-500">Failed to load status</div>
        ) : (
          <div className="p-6 sm:p-8 flex flex-col gap-8">

            {/* Health summary bar */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[
                { label: 'Cafes / hr', value: status.cafes_last_hour, sub: `${status.cafes_24h} today` },
                { label: 'Images / hr', value: status.images_last_hour, sub: `${status.images_24h} today` },
                { label: 'Total cafes', value: status.total_cafes.toLocaleString(), sub: `last: ${timeSince(status.last_cafe_at)}` },
                { label: 'Total images', value: status.total_images.toLocaleString(), sub: `last: ${timeSince(status.last_image_at)}` },
              ].map(card => (
                <div key={card.label} className="bg-gray-50 rounded-xl p-3 flex flex-col gap-1">
                  <span className="text-xs text-gray-500 font-medium uppercase tracking-wide">{card.label}</span>
                  <span className="text-2xl font-bold text-gray-900">{card.value}</span>
                  <span className="text-xs text-gray-400">{card.sub}</span>
                </div>
              ))}
            </div>

            {/* Service toggles */}
            <div>
              <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Services</h3>
              <div className="flex flex-col gap-2">
                {status.services.map(svc => (
                  <div key={svc.name} className="flex items-center justify-between bg-gray-50 rounded-xl px-4 py-3">
                    <div className="flex items-center gap-3">
                      <span
                        className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                        style={{ background: svc.active ? '#22c55e' : svc.state === 'failed' ? '#ef4444' : '#d1d5db' }}
                      />
                      <div>
                        <div className="font-medium text-gray-800 text-sm">{SERVICE_LABELS[svc.name] ?? svc.name}</div>
                        <div className="text-xs text-gray-400">{svc.unit} · {svc.state}</div>
                      </div>
                    </div>
                    <button
                      disabled={toggling[svc.name] || svc.name === 'api'}
                      onClick={() => toggle(svc.name, svc.active)}
                      title={svc.name === 'api' ? 'Cannot stop API from here' : undefined}
                      className={`relative w-11 h-6 rounded-full transition-colors duration-200 flex-shrink-0 ${
                        svc.active ? 'bg-green-500' : 'bg-gray-300'
                      } ${toggling[svc.name] ? 'opacity-50' : ''} ${svc.name === 'api' ? 'cursor-not-allowed opacity-40' : 'cursor-pointer'}`}
                    >
                      <span
                        className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform duration-200 ${
                          svc.active ? 'translate-x-5' : 'translate-x-0'
                        }`}
                      />
                    </button>
                  </div>
                ))}
              </div>
            </div>

            {/* Per-provider metrics */}
            <div className="mb-8">
              <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">Scraping Metrics</h3>
              <div className="border border-gray-100 rounded-xl overflow-x-auto bg-white">
                <table className="w-full text-sm min-w-[600px]">
                  <thead>
                    <tr className="bg-gray-50 text-left">
                      <th className="px-4 py-2.5 font-semibold text-gray-500 text-xs uppercase tracking-wide">Provider</th>
                      <th className="px-4 py-2.5 font-semibold text-gray-500 text-xs uppercase tracking-wide text-right">Total cafes</th>
                      <th className="px-4 py-2.5 font-semibold text-gray-500 text-xs uppercase tracking-wide text-right">Cafes/hr</th>
                      <th className="px-4 py-2.5 font-semibold text-gray-500 text-xs uppercase tracking-wide text-right">Cafes/24h</th>
                      <th className="px-4 py-2.5 font-semibold text-gray-500 text-xs uppercase tracking-wide text-right">Imgs/hr</th>
                      <th className="px-4 py-2.5 font-semibold text-gray-500 text-xs uppercase tracking-wide text-right">Imgs/24h</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(status.per_provider || []).map((p, i) => (
                      <tr key={p.provider} className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'}>
                        <td className="px-4 py-2.5 font-medium text-gray-800 capitalize">{p.provider}</td>
                        <td className="px-4 py-2.5 text-right text-gray-700">{p.total.toLocaleString()}</td>
                        <td className="px-4 py-2.5 text-right">
                          <span className={`font-medium ${p.cafes_last_hour > 0 ? 'text-green-600' : 'text-gray-400'}`}>
                            {p.cafes_last_hour}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-right text-gray-600">{p.cafes_24h}</td>
                        <td className="px-4 py-2.5 text-right">
                          <span className={`font-medium ${p.images_last_hour > 0 ? 'text-blue-600' : 'text-gray-400'}`}>
                            {p.images_last_hour}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-right text-gray-600">{p.images_24h}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Disk usage */}
            {status.disk && (
              <div>
                <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Storage</h3>
                <div className="bg-gray-50 rounded-xl px-4 py-3 flex flex-col gap-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-gray-700">{status.disk.data_dir_gb} GB used of {status.disk.limit_gb} GB limit</span>
                    <span className={`font-semibold ${status.disk.used_pct > 85 ? 'text-red-600' : status.disk.used_pct > 60 ? 'text-yellow-600' : 'text-green-600'}`}>
                      {status.disk.used_pct}%
                    </span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-2">
                    <div
                      className={`h-2 rounded-full transition-all ${status.disk.used_pct > 85 ? 'bg-red-500' : status.disk.used_pct > 60 ? 'bg-yellow-500' : 'bg-green-500'}`}
                      style={{ width: `${Math.min(status.disk.used_pct, 100)}%` }}
                    />
                  </div>
                </div>
              </div>
            )}

            {/* DB queue */}
            {status.db_queue && Object.keys(status.db_queue).length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">DB Write Queue</h3>
                <div className="flex flex-col gap-1.5">
                  {Object.entries(status.db_queue).map(([provider, entry]) => (
                    <div key={provider} className="flex items-center justify-between bg-gray-50 rounded-xl px-4 py-2.5 text-sm">
                      <span className="capitalize text-gray-700">{provider}</span>
                      <div className="flex items-center gap-3">
                        <span className={`font-semibold ${entry.queue_depth > 0 ? 'text-orange-600' : 'text-green-600'}`}>
                          {entry.queue_depth > 0 ? `${entry.queue_depth} queued` : 'clear'}
                        </span>
                        <span className="text-xs text-gray-400">{timeSince(entry.updated_at)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Health indicator */}
            <div className="flex items-center gap-3 text-sm text-gray-600 bg-gray-50 rounded-xl px-4 py-3">
              <span
                className="w-3 h-3 rounded-full flex-shrink-0"
                style={{ background: healthColor(status.last_cafe_at || status.last_image_at, scraperActive) }}
              />
              <span>
                {scraperActive
                  ? `Scrapers running · last cafe ${timeSince(status.last_cafe_at)} · last image ${timeSince(status.last_image_at)}`
                  : 'No scrapers active'}
              </span>
              <button
                onClick={fetchStatus}
                className="ml-auto text-xs text-blue-500 hover:text-blue-700 transition-colors"
              >
                Refresh
              </button>
            </div>

          </div>
        )}
      </div>
    </div>
  )
}
