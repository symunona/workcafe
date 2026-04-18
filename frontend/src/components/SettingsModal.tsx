import { useEffect, useState, useCallback, useMemo } from 'react'
import { CloseIcon } from './Icons'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts'

interface HourlyStat {
  hour: string
  cafes: number
  images: number
  provider: string
}

interface ServiceStatus {
  name: string
  unit: string
  state: string
  active: boolean
  exit_status?: string  // "success" | "killed" | "failed" | undefined
  last_log?: string
}

interface ProviderMetrics {
  provider: string
  cafes_last_hour: number
  cafes_24h: number
  images_last_hour: number
  images_24h: number
  total: number
  cafes_with_images: number
  cafes_2plus: number
  cafes_10plus: number
  cafes_50plus: number
  avg_images: number
  total_images: number
}

interface DiskStats {
  data_dir_gb: number
  limit_gb: number
  used_pct: number
  free_gb: number
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
  hourly_stats: HourlyStat[]
}

const SERVICE_LABELS: Record<string, string> = {
  kakao: 'Kakao Scraper',
  google: 'Google Scraper',
  osm: 'OSM Scraper',
  naver: 'Naver Scraper',
  imagescraper: 'Image Scraper (Kakao)',
  naver_images: 'Image Scraper (Naver)',
  api: 'API Server',
  frontend: 'Frontend',
}

const PROVIDER_COLORS: Record<string, string> = {
  kakao: '#facc15', // bright yellow
  google: '#ef4444', // red
  naver: '#10b981', // emerald green
  osm: '#d946ef', // vibrant purple
  all: '#64748b', // slate
}

function serviceInactiveMood(svc: ServiceStatus): 'success' | 'sleeping' | 'error' | 'killed' | 'stopped' {
  if (svc.exit_status === 'success') return 'success'
  if (svc.exit_status === 'failed' || svc.state === 'failed') return 'error'
  if (svc.exit_status === 'killed') return 'killed'
  if (!svc.last_log) return 'stopped'
  const l = svc.last_log.toLowerCase()
  if (/sleep|sleeping|waiting|rate.?limit|backoff|pausing|throttl|retry/.test(l)) return 'sleeping'
  if (/error|exception|traceback|critical|failed|fatal|crash/.test(l)) return 'error'
  return 'stopped'
}

function timeSince(iso: string): string {
  if (!iso) return 'never'
  let dStr = iso.replace(' ', 'T')
  // If it doesn't have a Z and doesn't have a timezone offset (+00:00 or -00:00), append Z
  if (!dStr.includes('Z') && !dStr.match(/[+-]\d{2}:\d{2}$/)) dStr += 'Z'
  const time = new Date(dStr).getTime()
  if (isNaN(time)) return 'invalid date'
  const diff = Date.now() - time
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function healthColor(lastAt: string, activeServices: boolean): string {
  if (!lastAt) return '#ef4444'
  let dStr = lastAt.replace(' ', 'T')
  if (!dStr.includes('Z') && !dStr.match(/[+-]\d{2}:\d{2}$/)) dStr += 'Z'
  const diffH = (Date.now() - new Date(dStr).getTime()) / 3600000
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

  const chartData = useMemo(() => {
    if (!status?.hourly_stats) return []
    const map = new Map<string, any>()
    status.hourly_stats.forEach(s => {
      if (s.provider === 'all') return
      if (!map.has(s.hour)) map.set(s.hour, { hour: s.hour })
      const row = map.get(s.hour)
      row[`${s.provider}_cafes`] = s.cafes
      row[`${s.provider}_images`] = s.images
    })
    const sorted = Array.from(map.values()).sort((a, b) => a.hour.localeCompare(b.hour))
    // Drop the last bucket — it's the current partial hour and always looks like a drop
    return sorted.slice(0, -1)
  }, [status?.hourly_stats])

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

  const scraperActive = status?.services.some(s => (s.name === 'datascraper' || s.name === 'imagescraper' || s.name === 'naver_images') && s.active) ?? false

  return (
    <div className="fixed inset-0 z-[2000] bg-black/60 flex items-center justify-center p-4 sm:p-6 backdrop-blur-sm animate-in fade-in">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-h-full max-w-3xl overflow-y-auto flex flex-col relative">
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

            {/* Service toggles & DB Queue */}
            <div className="mb-8">
              <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Services &amp; DB Queue</h3>
              <div className="flex flex-col gap-2">
                {status.services.map(svc => {
                  const mood = !svc.active ? serviceInactiveMood(svc) : null
                  const rowBg = svc.active ? 'bg-gray-50'
                    : mood === 'success' ? 'bg-green-50/70'
                    : mood === 'sleeping' ? 'bg-yellow-50/70'
                    : mood === 'error' ? 'bg-red-50/60'
                    : mood === 'killed' ? 'bg-gray-100'
                    : 'bg-gray-50'
                  const dotColor = svc.active ? '#22c55e'
                    : mood === 'success' ? '#22c55e'
                    : mood === 'sleeping' ? '#eab308'
                    : mood === 'error' ? '#ef4444'
                    : '#d1d5db'
                  const logTextColor = svc.active ? 'text-gray-400'
                    : mood === 'success' ? 'text-green-700'
                    : mood === 'sleeping' ? 'text-yellow-700'
                    : mood === 'error' ? 'text-red-600'
                    : 'text-gray-500'
                  const moodLabel = !svc.active && mood === 'success' ? ' · completed'
                    : !svc.active && mood === 'killed' ? ' · stopped'
                    : ''
                  return (
                    <div key={svc.name} className={`flex items-center justify-between rounded-xl px-4 py-3 ${rowBg} ${(svc.last_log) ? 'flex-wrap gap-y-2' : ''}`}>
                      <div className="flex items-center gap-3 min-w-0 flex-1">
                        <span
                          className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                          style={{ background: dotColor }}
                        />
                        <div className="min-w-0">
                          <div className="font-medium text-gray-800 text-sm">{SERVICE_LABELS[svc.name] ?? svc.name}</div>
                          <div className="text-xs text-gray-500 mt-0.5">
                            <span>{svc.unit} · {svc.state}{moodLabel}</span>
                          </div>
                          {svc.last_log && (
                            <div className={`text-xs mt-1 font-mono leading-relaxed break-all ${logTextColor}`}>
                              {svc.last_log}
                            </div>
                          )}
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
                  )
                })}
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

            {/* Image coverage distribution */}
            {(status.per_provider || []).some(p => p.cafes_with_images > 0) && (
              <div className="mb-8">
                <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">Image Coverage</h3>
                <div className="border border-gray-100 rounded-xl overflow-x-auto bg-white">
                  <table className="w-full text-sm min-w-[580px]">
                    <thead>
                      <tr className="bg-gray-50 text-left">
                        <th className="px-4 py-2.5 font-semibold text-gray-500 text-xs uppercase tracking-wide">Provider</th>
                        <th className="px-4 py-2.5 font-semibold text-gray-500 text-xs uppercase tracking-wide text-right">Total cafes</th>
                        <th className="px-4 py-2.5 font-semibold text-gray-500 text-xs uppercase tracking-wide text-right">Has images</th>
                        <th className="px-4 py-2.5 font-semibold text-gray-500 text-xs uppercase tracking-wide text-right">2+</th>
                        <th className="px-4 py-2.5 font-semibold text-gray-500 text-xs uppercase tracking-wide text-right">10+</th>
                        <th className="px-4 py-2.5 font-semibold text-gray-500 text-xs uppercase tracking-wide text-right">50+</th>
                        <th className="px-4 py-2.5 font-semibold text-gray-500 text-xs uppercase tracking-wide text-right">Avg imgs</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(status.per_provider || []).filter(p => p.total > 0).map((p, i) => {
                        const pct = p.total > 0 ? Math.round(p.cafes_with_images / p.total * 100) : 0
                        return (
                          <tr key={p.provider} className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'}>
                            <td className="px-4 py-2.5 font-medium text-gray-800 capitalize">{p.provider}</td>
                            <td className="px-4 py-2.5 text-right text-gray-600">{p.total.toLocaleString()}</td>
                            <td className="px-4 py-2.5 text-right">
                              <span className={p.cafes_with_images > 0 ? 'text-blue-600 font-medium' : 'text-gray-400'}>
                                {p.cafes_with_images.toLocaleString()}
                              </span>
                              <span className="text-gray-400 text-xs ml-1">({pct}%)</span>
                            </td>
                            <td className="px-4 py-2.5 text-right text-gray-600">{p.cafes_2plus.toLocaleString()}</td>
                            <td className="px-4 py-2.5 text-right text-gray-600">{p.cafes_10plus.toLocaleString()}</td>
                            <td className="px-4 py-2.5 text-right text-gray-600">{p.cafes_50plus.toLocaleString()}</td>
                            <td className="px-4 py-2.5 text-right">
                              <span className={p.avg_images > 0 ? 'text-gray-700' : 'text-gray-400'}>
                                {p.avg_images > 0 ? p.avg_images.toFixed(1) : '—'}
                              </span>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Hourly Chart */}
            {chartData.length > 0 && (
              <div className="mb-8">
                <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">Scraping Activity (Last 24h)</h3>
                <div className="bg-white border border-gray-100 rounded-xl p-4 h-[350px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={chartData} margin={{ top: 5, right: 5, left: -20, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f3f4f6" />
                      <XAxis 
                        dataKey="hour" 
                        tickFormatter={(val) => {
                          const d = new Date(val.replace(' ', 'T') + 'Z')
                          return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                        }}
                        tick={{ fontSize: 12, fill: '#6b7280' }}
                        tickMargin={10}
                        axisLine={false}
                        tickLine={false}
                      />
                      <YAxis 
                        yAxisId="left"
                        tick={{ fontSize: 12, fill: '#6b7280' }}
                        tickMargin={10}
                        axisLine={false}
                        tickLine={false}
                      />
                      <YAxis 
                        yAxisId="right" 
                        orientation="right" 
                        tick={{ fontSize: 12, fill: '#6b7280' }}
                        tickMargin={10}
                        axisLine={false}
                        tickLine={false}
                      />
                      <Tooltip 
                        contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)' }}
                        labelFormatter={(val) => new Date(val.replace(' ', 'T') + 'Z').toLocaleString()}
                      />
                      <Legend wrapperStyle={{ fontSize: '12px' }} />
                      {status.per_provider.map(p => (
                        <Line key={`${p.provider}_cafes`} yAxisId="left" type="monotone" dataKey={`${p.provider}_cafes`} name={`${p.provider} Cafes`} stroke={PROVIDER_COLORS[p.provider] || '#000'} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
                      ))}
                      {status.per_provider.map(p => (
                        <Line key={`${p.provider}_images`} yAxisId="right" type="monotone" dataKey={`${p.provider}_images`} name={`${p.provider} Images`} stroke={PROVIDER_COLORS[p.provider] || '#000'} strokeWidth={2} strokeDasharray="5 5" dot={false} activeDot={{ r: 4 }} />
                      ))}
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}

            {/* Disk usage */}
            {status.disk && (
              <div>
                <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Storage</h3>
                <div className="bg-gray-50 rounded-xl px-4 py-3 flex flex-col gap-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-gray-700">{status.disk.free_gb} GB remaining of {status.disk.limit_gb} GB total</span>
                    <span className={`font-semibold ${status.disk.used_pct > 85 ? 'text-red-600' : status.disk.used_pct > 60 ? 'text-yellow-600' : 'text-green-600'}`}>
                      {status.disk.used_pct}% used
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
