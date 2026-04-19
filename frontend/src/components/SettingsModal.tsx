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
  exit_status?: string
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
  mb_per_day: number
}

const SERVICE_LABELS: Record<string, string> = {
  'db-server':     'DB Server',
  'api':           'API Server',
  'frontend':      'Frontend',
  'kakao':         'Kakao',
  'google':        'Google',
  'osm':           'OSM',
  'naver':         'Naver',
  'kakao-images':  'Kakao Images',
  'naver-images':  'Naver Images',
  'google-images': 'Google Images',
}

const SERVICE_GROUPS: { label: string; names: string[]; noToggle?: boolean }[] = [
  { label: 'Core', names: ['db-server', 'api', 'frontend'], noToggle: true },
  { label: 'Scrapers', names: ['kakao', 'google', 'osm', 'naver'] },
  { label: 'Image Scrapers', names: ['kakao-images', 'naver-images', 'google-images'] },
]

const PROVIDER_COLORS: Record<string, string> = {
  kakao:  '#facc15',
  google: '#ef4444',
  naver:  '#10b981',
  osm:    '#d946ef',
  all:    '#64748b',
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

function Spinner() {
  return <span className="w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin inline-block" />
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
      await new Promise(r => setTimeout(r, 1200))
      fetchStatus()
    } finally {
      setToggling(t => ({ ...t, [name]: false }))
    }
  }

  const svcByName = Object.fromEntries((status?.services ?? []).map(s => [s.name, s]))
  const scraperActive = status?.services.some(s =>
    ['kakao', 'google', 'osm', 'naver', 'kakao-images', 'naver-images', 'google-images'].includes(s.name) && s.active
  ) ?? false

  return (
    <div className="fixed inset-0 z-[2000] bg-black/60 flex items-end sm:items-center justify-center sm:p-6 backdrop-blur-sm animate-in fade-in">
      <div className="bg-white sm:rounded-2xl shadow-2xl w-full h-[95dvh] sm:h-auto sm:max-h-[90vh] sm:max-w-3xl flex flex-col overflow-hidden">

        {/* Header */}
        <div className="bg-white/90 backdrop-blur-md px-5 py-4 border-b border-gray-100 flex items-center justify-between shrink-0">
          <h2 className="text-lg font-bold text-gray-900">Scraper Status</h2>
          <button onClick={onClose} className="w-9 h-9 rounded-full bg-gray-100 flex items-center justify-center text-gray-500 hover:bg-gray-200 transition-colors">
            <CloseIcon />
          </button>
        </div>

        {loading ? (
          <div className="flex-1 flex items-center justify-center text-gray-400 gap-2">
            <Spinner /> Loading…
          </div>
        ) : !status ? (
          <div className="flex-1 flex items-center justify-center text-red-500">Failed to load status</div>
        ) : (
          <div className="flex-1 overflow-y-auto">
            <div className="p-4 sm:p-6 flex flex-col gap-5">

              {/* Summary cards */}
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                {[
                  { label: 'Cafes / hr', value: status.cafes_last_hour, sub: `${status.cafes_24h} today` },
                  { label: 'Images / hr', value: status.images_last_hour, sub: `${status.images_24h} today` },
                  { label: 'Total cafes', value: status.total_cafes.toLocaleString(), sub: `last ${timeSince(status.last_cafe_at)}` },
                  { label: 'Total images', value: status.total_images.toLocaleString(), sub: `last ${timeSince(status.last_image_at)}` },
                  { label: 'MB / day', value: status.mb_per_day ? `${status.mb_per_day.toLocaleString()} MB` : '—', sub: status.mb_per_day ? `~${Math.round(status.mb_per_day / 24)} MB/hr` : '' },
                ].map(card => (
                  <div key={card.label} className="bg-gray-50 rounded-xl p-3 flex flex-col gap-0.5">
                    <span className="text-xs text-gray-500 font-medium uppercase tracking-wide">{card.label}</span>
                    <span className="text-xl font-bold text-gray-900">{card.value}</span>
                    <span className="text-xs text-gray-400">{card.sub}</span>
                  </div>
                ))}
              </div>

              {/* Service groups */}
              <div className="flex flex-col gap-4">
                {SERVICE_GROUPS.map(group => {
                  const svcs = group.names.map(n => svcByName[n]).filter(Boolean)
                  return (
                    <div key={group.label}>
                      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1.5 px-1">{group.label}</h3>
                      <div className="flex flex-col gap-1">
                        {svcs.map(svc => {
                          const isToggling = toggling[svc.name]
                          const noToggle = group.noToggle || svc.name === 'api' || svc.name === 'frontend'
                          const mood = !svc.active ? serviceInactiveMood(svc) : null
                          const dotColor = svc.active ? '#22c55e'
                            : mood === 'sleeping' ? '#eab308'
                            : mood === 'error' ? '#ef4444'
                            : '#d1d5db'
                          const logColor = svc.active ? 'text-gray-400'
                            : mood === 'sleeping' ? 'text-yellow-700'
                            : mood === 'error' ? 'text-red-600'
                            : 'text-gray-400'
                          return (
                            <div
                              key={svc.name}
                              className={`flex items-center gap-3 rounded-xl px-3 py-2.5 transition-opacity ${
                                isToggling ? 'opacity-60' : ''
                              } ${svc.active ? 'bg-gray-50' : mood === 'error' ? 'bg-red-50/50' : 'bg-gray-50/60'}`}
                            >
                              {mood === 'success' ? (
                                <span className="w-5 h-5 rounded-full bg-green-100 border-2 border-green-400 flex items-center justify-center text-green-600 text-xs font-bold shrink-0">✓</span>
                              ) : (
                                <span className="w-2 h-2 rounded-full shrink-0" style={{ background: dotColor }} />
                              )}
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="text-sm font-medium text-gray-800">{SERVICE_LABELS[svc.name] ?? svc.name}</span>
                                  <span className="text-xs text-gray-400">{svc.state}</span>
                                  {isToggling && <Spinner />}
                                </div>
                                {svc.last_log && (
                                  <div className={`text-xs font-mono truncate mt-0.5 ${logColor}`} title={svc.last_log}>
                                    {svc.last_log}
                                  </div>
                                )}
                              </div>
                              {!noToggle && (
                                <button
                                  disabled={isToggling}
                                  onClick={() => toggle(svc.name, svc.active)}
                                  className={`relative w-10 h-5 rounded-full transition-colors duration-200 shrink-0 ${
                                    isToggling ? 'cursor-wait' : 'cursor-pointer'
                                  } ${svc.active ? 'bg-green-500' : 'bg-gray-300'}`}
                                >
                                  <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform duration-200 ${
                                    svc.active ? 'translate-x-5' : 'translate-x-0'
                                  }`} />
                                </button>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )
                })}
              </div>

              {/* Per-provider metrics */}
              {(status.per_provider || []).length > 0 && (
                <div>
                  <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1.5 px-1">Scraping Metrics</h3>
                  <div className="border border-gray-100 rounded-xl overflow-x-auto">
                    <table className="w-full text-sm min-w-[480px]">
                      <thead>
                        <tr className="bg-gray-50 text-left">
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase">Provider</th>
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase text-right">Total</th>
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase text-right">Cafes/hr</th>
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase text-right">Cafes/24h</th>
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase text-right">Imgs/hr</th>
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase text-right">Imgs/24h</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(status.per_provider || []).map((p, i) => (
                          <tr key={p.provider} className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'}>
                            <td className="px-3 py-2 font-medium text-gray-800 capitalize">{p.provider}</td>
                            <td className="px-3 py-2 text-right text-gray-700">{p.total.toLocaleString()}</td>
                            <td className="px-3 py-2 text-right">
                              <span className={p.cafes_last_hour > 0 ? 'text-green-600 font-medium' : 'text-gray-400'}>{p.cafes_last_hour}</span>
                            </td>
                            <td className="px-3 py-2 text-right text-gray-600">{p.cafes_24h}</td>
                            <td className="px-3 py-2 text-right">
                              <span className={p.images_last_hour > 0 ? 'text-blue-600 font-medium' : 'text-gray-400'}>{p.images_last_hour}</span>
                            </td>
                            <td className="px-3 py-2 text-right text-gray-600">{p.images_24h}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Image coverage */}
              {(status.per_provider || []).some(p => p.cafes_with_images > 0) && (
                <div>
                  <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1.5 px-1">Image Coverage</h3>
                  <div className="border border-gray-100 rounded-xl overflow-x-auto">
                    <table className="w-full text-sm min-w-[480px]">
                      <thead>
                        <tr className="bg-gray-50 text-left">
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase">Provider</th>
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase text-right">Total</th>
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase text-right">Has imgs</th>
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase text-right">2+</th>
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase text-right">10+</th>
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase text-right">50+</th>
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase text-right">Avg</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(status.per_provider || []).filter(p => p.total > 0).map((p, i) => {
                          const pct = p.total > 0 ? Math.round(p.cafes_with_images / p.total * 100) : 0
                          return (
                            <tr key={p.provider} className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'}>
                              <td className="px-3 py-2 font-medium text-gray-800 capitalize">{p.provider}</td>
                              <td className="px-3 py-2 text-right text-gray-600">{p.total.toLocaleString()}</td>
                              <td className="px-3 py-2 text-right">
                                <span className={p.cafes_with_images > 0 ? 'text-blue-600 font-medium' : 'text-gray-400'}>
                                  {p.cafes_with_images.toLocaleString()}
                                </span>
                                <span className="text-gray-400 text-xs ml-1">({pct}%)</span>
                              </td>
                              <td className="px-3 py-2 text-right text-gray-600">{p.cafes_2plus.toLocaleString()}</td>
                              <td className="px-3 py-2 text-right text-gray-600">{p.cafes_10plus.toLocaleString()}</td>
                              <td className="px-3 py-2 text-right text-gray-600">{p.cafes_50plus.toLocaleString()}</td>
                              <td className="px-3 py-2 text-right text-gray-600">{p.avg_images > 0 ? p.avg_images.toFixed(1) : '—'}</td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Hourly chart */}
              {chartData.length > 0 && (
                <div>
                  <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1.5 px-1">Activity (Last 24h)</h3>
                  <div className="bg-white border border-gray-100 rounded-xl p-3 h-[280px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={chartData} margin={{ top: 5, right: 5, left: -20, bottom: 5 }}>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f3f4f6" />
                        <XAxis
                          dataKey="hour"
                          tickFormatter={val => new Date(val.replace(' ', 'T') + 'Z').toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                          tick={{ fontSize: 11, fill: '#9ca3af' }}
                          axisLine={false} tickLine={false}
                        />
                        <YAxis yAxisId="left" tick={{ fontSize: 11, fill: '#9ca3af' }} axisLine={false} tickLine={false} />
                        <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11, fill: '#9ca3af' }} axisLine={false} tickLine={false} />
                        <Tooltip
                          contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgba(0,0,0,0.1)' }}
                          labelFormatter={val => new Date(val.replace(' ', 'T') + 'Z').toLocaleString()}
                        />
                        <Legend wrapperStyle={{ fontSize: '11px' }} />
                        {status.per_provider.map(p => (
                          <Line key={`${p.provider}_cafes`} yAxisId="left" type="monotone" dataKey={`${p.provider}_cafes`} name={`${p.provider} cafes`} stroke={PROVIDER_COLORS[p.provider] || '#000'} strokeWidth={2} dot={false} activeDot={{ r: 3 }} />
                        ))}
                        {status.per_provider.map(p => (
                          <Line key={`${p.provider}_images`} yAxisId="right" type="monotone" dataKey={`${p.provider}_images`} name={`${p.provider} imgs`} stroke={PROVIDER_COLORS[p.provider] || '#000'} strokeWidth={1.5} strokeDasharray="4 4" dot={false} activeDot={{ r: 3 }} />
                        ))}
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}

              {/* Disk + health */}
              {status.disk && (
                <div className="bg-gray-50 rounded-xl px-4 py-3 flex flex-col gap-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-gray-600">Storage — {status.disk.free_gb} GB free of {status.disk.limit_gb} GB</span>
                    <span className={`font-semibold text-sm ${status.disk.used_pct > 85 ? 'text-red-600' : status.disk.used_pct > 60 ? 'text-yellow-600' : 'text-green-600'}`}>
                      {status.disk.used_pct}%
                    </span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-1.5">
                    <div
                      className={`h-1.5 rounded-full transition-all ${status.disk.used_pct > 85 ? 'bg-red-500' : status.disk.used_pct > 60 ? 'bg-yellow-500' : 'bg-green-500'}`}
                      style={{ width: `${Math.min(status.disk.used_pct, 100)}%` }}
                    />
                  </div>
                </div>
              )}

              <div className="flex items-center gap-3 text-sm text-gray-600 bg-gray-50 rounded-xl px-4 py-3">
                <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: healthColor(status.last_cafe_at || status.last_image_at, scraperActive) }} />
                <span className="flex-1 text-xs">
                  {scraperActive
                    ? `Scrapers active · last cafe ${timeSince(status.last_cafe_at)} · last image ${timeSince(status.last_image_at)}`
                    : 'No scrapers active'}
                </span>
                <button onClick={fetchStatus} className="text-xs text-blue-500 hover:text-blue-700 transition-colors shrink-0">Refresh</button>
              </div>

            </div>
          </div>
        )}
      </div>
    </div>
  )
}
