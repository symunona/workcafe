import { useEffect, useState, useCallback, useMemo, useRef } from 'react'
import { CloseIcon } from './Icons'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts'

interface WatchdogService {
  unit: string
  active: boolean
  active_state: string
  pid: number
  last_log_at: string | null
  last_log_age_s: number | null
  stale: boolean
  healthy: boolean
  auto_restarts: number
  last_watchdog_restart: string | null
  error?: string
}

interface WatchdogStatus {
  updated_at: string | null
  stale_threshold_minutes: number
  services: Record<string, WatchdogService>
}

interface HourlyStat {
  hour: string
  scraped_cafes: number
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
  downloaded_last_hour: number
  downloaded_24h: number
  total: number
  cafes_with_images: number
  cafes_2plus: number
  cafes_10plus: number
  cafes_50plus: number
  avg_images: number
  has_website?: number
  total_images: number
}


interface DiskStats {
  data_dir_gb: number
  folder_size_gb: number
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
  downloaded_last_hour: number
  downloaded_24h: number
  last_cafe_at: string
  last_image_at: string
  disk: DiskStats
  db_queue: Record<string, QueueEntry>
  hourly_stats: HourlyStat[]
  mb_per_day: number
  overall_tagged_images?: number
  overall_imgs_per_hour?: number
}

const SERVICE_LABELS: Record<string, string> = {
  'db-server':       'DB Server',
  'api':             'API Server',
  'frontend':        'Frontend',
  'kakao':           'Kakao',
  'google':          'Google',
  'osm':             'OSM',
  'naver':           'Naver',
  'kakao-images':    'Kakao Images',
  'naver-images':    'Naver Images',
  'google-images':   'Google Images',
  'kakao-metadata':  'Kakao Metadata',
  'naver-metadata':  'Naver Metadata',
}

const SERVICE_GROUPS: { label: string; names: string[]; noToggle?: boolean }[] = [
  { label: 'Core', names: ['db-server', 'api', 'frontend'], noToggle: true },
  { label: 'Scrapers', names: ['kakao', 'google', 'osm', 'naver'] },
  { label: 'Image Scrapers', names: ['kakao-images', 'naver-images', 'google-images'] },
  { label: 'Metadata Scrapers', names: ['kakao-metadata', 'naver-metadata'] },
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
  showToggles?: boolean
}

const IMAGE_SCRAPERS = new Set(['kakao-images', 'naver-images', 'google-images'])

interface ServiceTech {
  method: string      // scraping technique
  antibot: string     // countermeasures
  parallel?: string   // parallelism / concurrency model
}

const SERVICE_TECH: Record<string, ServiceTech> = {
  'kakao': {
    method: 'Headless Chromium (Playwright) · intercepts Kakao searchJson API via response listener · mobile UA (Samsung Galaxy S20)',
    antibot: 'Random 2–4s delays between grids · 300s watchdog kills hangs · Kakao\'s grid search requires browser to render search page',
  },
  'google': {
    method: 'Headless Chromium · parses Google Maps search result links + coordinates from DOM · visits detail page per place for image refs',
    antibot: 'No Tor at this stage (Tor used by google-images) · consent popup handler · random delays',
  },
  'naver': {
    method: 'Headless Chromium · fills Naver Maps search box → intercepts allSearch JSON API response · desktop Chrome UA',
    antibot: 'SIGTERM graceful exit · 300s watchdog per grid · random 2–4s delays · retries on empty API response',
  },
  'osm': {
    method: 'Pure HTTP · Overpass API queries Seoul bounding box for amenity=cafe nodes/ways · no browser needed',
    antibot: 'Tor SOCKS5 proxy (port 9050) for IP anonymity · exponential backoff on 429/5xx · retry adapter',
  },
  'kakao-images': {
    method: 'Pure HTTP — no browser · Kakao Photo REST API (place-api.map.kakao.com/places/tab/photos/{id}) · paginated per place',
    antibot: 'Random User-Agent pool (3 mobile UAs) · 0.1–0.2s sleep per request · retry on 429 with 5s backoff · watchdog restarts on stale log',
    parallel: 'Sequential per-cafe, paginated API — up to 120 photos/page across 7 cursor types',
  },
  'naver-images': {
    method: 'Headless Chromium · GraphQL calls via page.evaluate() browser fetch — reuses browser cookies/headers · auto business_type detection (restaurant → place fallback)',
    antibot: '429 detection with exponential backoff up to 120s · Naver UA-bans bare HTTP to pcmap-api; browser context bypasses this · watchdog restarts on stale log',
    parallel: 'Single browser session, sequential cafes, paginated GraphQL cursors (biz/clip/visitorReview/…)',
  },
  'google-images': {
    method: 'Headless Chromium · Tor SOCKS5 proxy rotation · navigates to Google Maps place → clicks Photos tab → extracts image URLs from DOM',
    antibot: 'Tor exit IP rotation via stem NEWNYM on 429/captcha · captcha page detection · consent popup dismissal · random 3–8s delays · per-proxy stats tracked in google-proxy-stats.json',
    parallel: 'N parallel browser workers (configurable), each with own Tor circuit',
  },
  'kakao-metadata': {
    method: 'Pure HTTP · Kakao Place API (place-api.map.kakao.com/places/panel3/{id}) with pf:MW header · extracts homepages[], phone_numbers[], address.road, open_hours per place',
    antibot: '3 random mobile UAs · 0.15s sleep per request · 10s backoff on 429 · marks metadata_last_checked to skip re-processed entries',
    parallel: '15 concurrent threads, each with own requests.Session',
  },
  'naver-metadata': {
    method: 'Phase 1: SQL extraction of homePage/tel/roadAddress already in stored allSearch metadata (covers ~67%) · Phase 2: Naver Place Summary API for remainder',
    antibot: '0.2s sleep per request · 15s backoff on 429 · metadata_last_checked prevents re-scraping within 30 days',
    parallel: '10 concurrent threads (Phase 2 only)',
  },
}

export function SettingsModal({ onClose, showToggles = false }: SettingsModalProps) {
  const [status, setStatus] = useState<StatusData | null>(null)
  const [watchdog, setWatchdog] = useState<WatchdogStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [toggling, setToggling] = useState<Record<string, boolean>>({})
  const [expanded, setExpanded] = useState<string | null>(null)
  const [logLines, setLogLines] = useState<Record<string, string[]>>({})
  const [logLoading, setLogLoading] = useState<Record<string, boolean>>({})
  const logFetched = useRef<Set<string>>(new Set())

  const chartData = useMemo(() => {
    if (!status?.hourly_stats) return []
    const map = new Map<string, any>()
    status.hourly_stats.forEach(s => {
      if (s.provider === 'all') return
      if (!map.has(s.hour)) map.set(s.hour, { hour: s.hour })
      const row = map.get(s.hour)
      row[`${s.provider}_cafes`] = s.scraped_cafes
      row[`${s.provider}_images`] = s.images
    })
    const sorted = Array.from(map.values()).sort((a, b) => a.hour.localeCompare(b.hour))
    return sorted.slice(0, -1)
  }, [status?.hourly_stats])

  const fetchStatus = useCallback(() => {
    Promise.all([
      fetch('/api/status').then(r => r.json()),
      fetch('/api/watchdog-status').then(r => r.json()).catch(() => null),
    ]).then(([s, w]) => {
      setStatus(s)
      setWatchdog(w)
      setLoading(false)
    }).catch(() => setLoading(false))
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

  async function toggleGroup(names: string[], action: 'start' | 'stop') {
    const mark = Object.fromEntries(names.map(n => [n, true]))
    setToggling(t => ({ ...t, ...mark }))
    try {
      await Promise.all(names.map(n => fetch(`/api/services/${n}/${action}`, { method: 'POST' })))
      await new Promise(r => setTimeout(r, 1500))
      fetchStatus()
    } finally {
      setToggling(t => { const next = { ...t }; names.forEach(n => delete next[n]); return next })
    }
  }

  function fetchLog(name: string) {
    if (logFetched.current.has(name)) return
    logFetched.current.add(name)
    setLogLoading(l => ({ ...l, [name]: true }))
    fetch(`/api/services/${name}/log?lines=25`)
      .then(r => r.json())
      .then((d: { lines?: string[] }) => setLogLines(l => ({ ...l, [name]: d.lines ?? [] })))
      .catch(() => setLogLines(l => ({ ...l, [name]: ['(failed to load log)'] })))
      .finally(() => setLogLoading(l => ({ ...l, [name]: false })))
  }

  function toggleExpand(name: string) {
    if (expanded === name) {
      setExpanded(null)
    } else {
      setExpanded(name)
      fetchLog(name)
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
          <div className="flex-1 overflow-y-auto">
            <div className="p-4 sm:p-6 flex flex-col gap-5 animate-pulse">
              {/* Summary cards skeleton */}
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="bg-gray-100 rounded-xl p-3 flex flex-col gap-2">
                    <div className="h-2.5 w-16 bg-gray-200 rounded" />
                    <div className="h-6 w-10 bg-gray-300 rounded" />
                    <div className="h-2 w-12 bg-gray-200 rounded" />
                  </div>
                ))}
              </div>
              {/* Chart skeleton */}
              <div>
                <div className="h-2.5 w-32 bg-gray-200 rounded mb-2" />
                <div className="bg-gray-100 rounded-xl h-[280px]" />
              </div>
              {/* Disk skeleton */}
              <div className="bg-gray-100 rounded-xl px-4 py-3 flex flex-col gap-2">
                <div className="h-3 w-48 bg-gray-200 rounded" />
                <div className="w-full bg-gray-200 rounded-full h-1.5" />
                <div className="h-3 w-32 bg-gray-200 rounded mt-1" />
              </div>
              {/* Service groups skeleton */}
              {['Core', 'Scrapers', 'Image Scrapers'].map(label => (
                <div key={label}>
                  <div className="h-2.5 w-20 bg-gray-200 rounded mb-2" />
                  <div className="flex flex-col gap-1">
                    {Array.from({ length: label === 'Core' ? 3 : label === 'Scrapers' ? 4 : 3 }).map((_, i) => (
                      <div key={i} className="flex items-center gap-3 bg-gray-100 rounded-xl px-3 py-2.5">
                        <div className="w-2 h-2 rounded-full bg-gray-300 shrink-0" />
                        <div className="flex-1 flex flex-col gap-1.5">
                          <div className="h-3 w-24 bg-gray-200 rounded" />
                          <div className="h-2 w-40 bg-gray-200 rounded" />
                        </div>
                        <div className="w-10 h-5 bg-gray-200 rounded-full shrink-0" />
                      </div>
                    ))}
                  </div>
                </div>
              ))}
              {/* Metrics table skeleton */}
              <div>
                <div className="h-2.5 w-28 bg-gray-200 rounded mb-2" />
                <div className="border border-gray-100 rounded-xl overflow-hidden">
                  {Array.from({ length: 5 }).map((_, i) => (
                    <div key={i} className={`flex gap-3 px-3 py-2 ${i === 0 ? 'bg-gray-100' : i % 2 === 0 ? 'bg-white' : 'bg-gray-50'}`}>
                      <div className="h-3 w-12 bg-gray-200 rounded" />
                      <div className="flex-1 flex justify-end gap-6">
                        {Array.from({ length: 5 }).map((_, j) => (
                          <div key={j} className="h-3 w-8 bg-gray-200 rounded" />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
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
                  { label: 'Imgs DL / hr', value: status.downloaded_last_hour, sub: `${status.downloaded_24h} today` },
                  { label: 'Total cafes', value: status.total_cafes.toLocaleString(), sub: `last ${timeSince(status.last_cafe_at)}` },
                  { label: 'Total images', value: status.total_images.toLocaleString(), sub: `last ${timeSince(status.last_image_at)}` },
                  { label: 'GB / day', value: status.mb_per_day ? `${(status.mb_per_day / 1024).toFixed(1)} GB` : '—', sub: status.mb_per_day ? `~${Math.round(status.mb_per_day / 24)} MB/hr` : '' },
                ].map(card => (
                  <div key={card.label} className="bg-gray-50 rounded-xl p-3 flex flex-col gap-0.5">
                    <span className="text-xs text-gray-500 font-medium uppercase tracking-wide">{card.label}</span>
                    <span className="text-xl font-bold text-gray-900">{card.value}</span>
                    <span className="text-xs text-gray-400">{card.sub}</span>
                  </div>
                ))}
              </div>

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
                          <Line key={`${p.provider}_cafes`} yAxisId="left" type="monotone" dataKey={`${p.provider}_cafes`} name={`${p.provider} scraped_cafes`} stroke={PROVIDER_COLORS[p.provider] || '#000'} strokeWidth={2} dot={false} activeDot={{ r: 3 }} />
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
                  <div className="flex items-center justify-between text-sm pt-1 border-t border-gray-200/60 mt-1">
                    <span className="text-gray-600">Data folder size</span>
                    <span className="font-semibold text-gray-700">{status.disk.folder_size_gb} GB</span>
                  </div>
                </div>
              )}

              {/* Service groups */}
              <div className="flex flex-col gap-4">
                {SERVICE_GROUPS.map(group => {
                  const svcs = group.names.map(n => svcByName[n]).filter(Boolean)
                  const toggleableNames = group.noToggle ? [] : group.names.filter(n => n !== 'api' && n !== 'frontend')
                  const allActive = toggleableNames.length > 0 && toggleableNames.every(n => svcByName[n]?.active)
                  const anyActive = toggleableNames.some(n => svcByName[n]?.active)
                  const groupToggling = toggleableNames.some(n => toggling[n])
                  return (
                    <div key={group.label}>
                      <div className="flex items-center justify-between mb-1.5 px-1">
                        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">{group.label}</h3>
                        {showToggles && toggleableNames.length > 0 && (
                          <div className="flex gap-1">
                            {!allActive && (
                              <button
                                disabled={groupToggling}
                                onClick={() => toggleGroup(toggleableNames, 'start')}
                                className="text-xs px-2 py-0.5 rounded-md bg-green-100 text-green-700 hover:bg-green-200 disabled:opacity-50 transition-colors font-medium"
                              >
                                {groupToggling ? <Spinner /> : '▶ Start all'}
                              </button>
                            )}
                            {anyActive && (
                              <button
                                disabled={groupToggling}
                                onClick={() => toggleGroup(toggleableNames, 'stop')}
                                className="text-xs px-2 py-0.5 rounded-md bg-red-100 text-red-700 hover:bg-red-200 disabled:opacity-50 transition-colors font-medium"
                              >
                                {groupToggling ? <Spinner /> : '■ Stop all'}
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                      <div className="flex flex-col gap-1">
                        {svcs.map(svc => {
                          const isToggling = toggling[svc.name]
                          const noToggle = group.noToggle || svc.name === 'api' || svc.name === 'frontend'
                          const mood = !svc.active ? serviceInactiveMood(svc) : null
                          const wd = IMAGE_SCRAPERS.has(svc.name) ? watchdog?.services?.[svc.name] : null
                          const dotColor = svc.active
                            ? (wd?.stale ? '#f97316' : '#22c55e')
                            : mood === 'sleeping' ? '#eab308'
                            : mood === 'error' ? '#ef4444'
                            : '#d1d5db'
                          const logColor = svc.active ? 'text-gray-400'
                            : mood === 'sleeping' ? 'text-yellow-700'
                            : mood === 'error' ? 'text-red-600'
                            : 'text-gray-400'
                          const isExpanded = expanded === svc.name
                          const tech = SERVICE_TECH[svc.name]
                          const bgClass = svc.active ? (wd?.stale ? 'bg-orange-50/60' : 'bg-gray-50') : mood === 'error' ? 'bg-red-50/50' : 'bg-gray-50/60'
                          return (
                            <div key={svc.name} className={`rounded-xl overflow-hidden ${isToggling ? 'opacity-60' : ''}`}>
                              {/* Main row — click to expand */}
                              <div
                                className={`flex items-center gap-3 px-3 py-2.5 cursor-pointer ${bgClass} ${isExpanded ? 'border-b border-gray-200/60' : ''}`}
                                onClick={() => toggleExpand(svc.name)}
                              >
                                {mood === 'success' ? (
                                  <span className="w-5 h-5 rounded-full bg-green-100 border-2 border-green-400 flex items-center justify-center text-green-600 text-xs font-bold shrink-0">✓</span>
                                ) : (
                                  <span className="w-2 h-2 rounded-full shrink-0" style={{ background: dotColor }} />
                                )}
                                <div className="flex-1 min-w-0">
                                  <div className="flex items-center gap-2 flex-wrap">
                                    <span className="text-sm font-medium text-gray-800">{SERVICE_LABELS[svc.name] ?? svc.name}</span>
                                    <span className="text-xs text-gray-400">{svc.state}</span>
                                    {isToggling && <Spinner />}
                                    {wd && wd.auto_restarts > 0 && (
                                      <span
                                        className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${wd.stale ? 'bg-red-100 text-red-700' : 'bg-orange-100 text-orange-700'}`}
                                        title={wd.last_watchdog_restart ? `Last auto-restart: ${new Date(wd.last_watchdog_restart).toLocaleString()}` : ''}
                                      >
                                        ↺ {wd.auto_restarts}
                                      </span>
                                    )}
                                    {wd?.stale && (
                                      <span className="text-xs px-1.5 py-0.5 rounded-full bg-orange-100 text-orange-700 font-medium"
                                            title={`Last log: ${wd.last_log_at ? timeSince(wd.last_log_at) : 'unknown'}`}>
                                        stale {wd.last_log_age_s != null ? `${Math.round(wd.last_log_age_s / 60)}m` : ''}
                                      </span>
                                    )}
                                  </div>
                                  {svc.last_log && !isExpanded && (
                                    <div className={`text-xs font-mono truncate mt-0.5 ${logColor}`} title={svc.last_log}>
                                      {svc.last_log}
                                    </div>
                                  )}
                                  {wd && !wd.stale && wd.last_log_at && !isExpanded && (
                                    <div className="text-xs text-gray-400 mt-0.5">
                                      last log {timeSince(wd.last_log_at)}
                                    </div>
                                  )}
                                </div>
                                <span className="text-gray-300 text-xs shrink-0 select-none">{isExpanded ? '▲' : '▼'}</span>
                                {showToggles && !noToggle && (
                                  <button
                                    disabled={isToggling}
                                    onClick={e => { e.stopPropagation(); toggle(svc.name, svc.active) }}
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

                              {/* Accordion content */}
                              {isExpanded && (
                                <div className={`${bgClass} px-4 pb-3 pt-2 flex flex-col gap-2`}>
                                  {tech && (
                                    <div className="flex flex-col gap-1.5">
                                      <div className="flex gap-2 text-xs">
                                        <span className="text-gray-400 font-medium shrink-0 w-16">Method</span>
                                        <span className="text-gray-700">{tech.method}</span>
                                      </div>
                                      <div className="flex gap-2 text-xs">
                                        <span className="text-gray-400 font-medium shrink-0 w-16">Anti-bot</span>
                                        <span className="text-gray-700">{tech.antibot}</span>
                                      </div>
                                      {tech.parallel && (
                                        <div className="flex gap-2 text-xs">
                                          <span className="text-gray-400 font-medium shrink-0 w-16">Parallel</span>
                                          <span className="text-gray-700">{tech.parallel}</span>
                                        </div>
                                      )}
                                    </div>
                                  )}
                                  <div className="mt-1">
                                    <div className="flex items-center justify-between mb-1">
                                      <span className="text-xs font-medium text-gray-400 uppercase tracking-wide">Recent log</span>
                                      <button
                                        onClick={() => { logFetched.current.delete(svc.name); fetchLog(svc.name) }}
                                        className="text-xs text-blue-400 hover:text-blue-600"
                                      >↻ refresh</button>
                                    </div>
                                    {logLoading[svc.name] ? (
                                      <div className="text-xs text-gray-400 font-mono">Loading…</div>
                                    ) : logLines[svc.name]?.length ? (
                                      <div className="bg-gray-900 rounded-lg px-3 py-2 max-h-40 overflow-y-auto">
                                        {logLines[svc.name].map((line, i) => (
                                          <div key={i} className="text-xs font-mono text-gray-300 leading-relaxed whitespace-pre-wrap break-all">{line}</div>
                                        ))}
                                      </div>
                                    ) : (
                                      <div className="text-xs text-gray-400 font-mono italic">No log file found</div>
                                    )}
                                  </div>
                                </div>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )
                })}
              </div>

              {/* Watchdog status footer */}
              {watchdog && (
                <div className="text-xs text-gray-400 px-1 -mt-2 flex items-center gap-1.5">
                  <span className={`w-1.5 h-1.5 rounded-full inline-block ${
                    Object.values(watchdog.services).some(s => s.stale) ? 'bg-orange-400' : 'bg-green-400'
                  }`} />
                  Watchdog {watchdog.updated_at ? `checked ${timeSince(watchdog.updated_at)}` : 'not yet run'}
                  {watchdog.stale_threshold_minutes && ` · stale threshold ${watchdog.stale_threshold_minutes}m`}
                </div>
              )}

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
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase text-right" title="Has website URL in metadata">Website</th>
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase text-right">Cafes/hr</th>
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase text-right">Cafes/24h</th>
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase text-right" title="Images saved to disk (file_size > 0)">DL/hr</th>
                          <th className="px-3 py-2 font-semibold text-gray-500 text-xs uppercase text-right" title="Images saved to disk (file_size > 0)">DL/24h</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(status.per_provider || []).map((p, i) => (
                          <tr key={p.provider} className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'}>
                            <td className="px-3 py-2 font-medium text-gray-800 capitalize">{p.provider}</td>
                            <td className="px-3 py-2 text-right text-gray-700">{p.total.toLocaleString()}</td>
                            <td className="px-3 py-2 text-right">
                              {(p.has_website ?? 0) > 0 ? (
                                <>
                                  <span className="text-emerald-600 font-medium">{(p.has_website ?? 0).toLocaleString()}</span>
                                  <span className="text-gray-400 text-xs ml-1">({p.total > 0 ? Math.round((p.has_website ?? 0) / p.total * 100) : 0}%)</span>
                                </>
                              ) : <span className="text-gray-300">—</span>}
                            </td>
                            <td className="px-3 py-2 text-right">
                              <span className={p.cafes_last_hour > 0 ? 'text-green-600 font-medium' : 'text-gray-400'}>{p.cafes_last_hour}</span>
                            </td>
                            <td className="px-3 py-2 text-right text-gray-600">{p.cafes_24h}</td>
                            <td className="px-3 py-2 text-right">
                              <span className={p.downloaded_last_hour > 0 ? 'text-violet-600 font-medium' : 'text-gray-400'}>{p.downloaded_last_hour}</span>
                            </td>
                            <td className="px-3 py-2 text-right text-gray-600">{p.downloaded_24h}</td>
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



              {/* Image tagger progress */}
              {(status.overall_tagged_images ?? 0) > 0 && (
                <div>
                  <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1.5 px-1">Image Tagging</h3>
                  <div className="bg-gray-50 rounded-xl px-4 py-3 flex flex-col gap-1.5">
                    {/* Overall row */}
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-gray-800">Overall</span>
                      <div className="flex items-center gap-3 text-xs">
                        {(status.overall_imgs_per_hour ?? 0) > 0 && (
                          <span className="text-violet-600 font-medium">{(status.overall_imgs_per_hour ?? 0).toLocaleString()} imgs/hr</span>
                        )}
                        <span className="text-gray-500">{(status.overall_tagged_images ?? 0).toLocaleString()} / {status.total_images.toLocaleString()}</span>
                        <span className="font-semibold text-gray-700">
                          {status.total_images > 0 ? ((status.overall_tagged_images ?? 0) / status.total_images * 100).toFixed(1) : '0'}%
                        </span>
                      </div>
                    </div>
                    <div className="w-full bg-gray-200 rounded-full h-1.5">
                      <div
                        className="h-1.5 rounded-full bg-violet-500 transition-all"
                        style={{ width: `${status.total_images > 0 ? Math.min((status.overall_tagged_images ?? 0) / status.total_images * 100, 100) : 0}%` }}
                      />
                    </div>
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
