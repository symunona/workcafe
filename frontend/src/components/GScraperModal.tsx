import { useEffect, useRef, useState, useCallback } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer
} from 'recharts'
import { CloseIcon } from './Icons'

interface ProxySummaryEntry {
  captcha: number
  success: number
  nav_error: number
  no_images: number
  images_total: number
}

interface ProxyEvent {
  ts: string
  proxy: string
  cafe_id: string
  outcome: string
  images?: number
}

interface StatsData {
  summary: Record<string, ProxySummaryEntry>
  events: ProxyEvent[]
}

const OUTCOME_COLORS: Record<string, string> = {
  success:   '#22c55e',
  captcha:   '#ef4444',
  no_images: '#f59e0b',
  nav_error: '#6b7280',
}

export function GScraperModal({ onClose }: { onClose: () => void }) {
  const [stats, setStats] = useState<StatsData | null>(null)
  const [logLines, setLogLines] = useState<string[]>([])
  const [logTotal, setLogTotal] = useState(0)
  const [tab, setTab] = useState<'stats' | 'log'>('stats')
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [loading, setLoading] = useState(true)
  const logRef = useRef<HTMLDivElement>(null)

  const fetchStats = useCallback(async () => {
    try {
      const r = await fetch('/api/gscraper/stats')
      const d: StatsData = await r.json()
      setStats(d)
    } catch {}
  }, [])

  const fetchLog = useCallback(async () => {
    try {
      const r = await fetch('/api/gscraper/log?lines=300')
      const d = await r.json()
      setLogLines(d.lines ?? [])
      setLogTotal(d.total ?? 0)
    } catch {}
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    await Promise.all([fetchStats(), fetchLog()])
    setLoading(false)
  }, [fetchStats, fetchLog])

  useEffect(() => { refresh() }, [refresh])

  // Auto-scroll log to bottom when new lines arrive
  useEffect(() => {
    if (tab === 'log' && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [logLines, tab])

  // Auto-refresh every 10s
  useEffect(() => {
    if (!autoRefresh) return
    const id = setInterval(refresh, 10_000)
    return () => clearInterval(id)
  }, [autoRefresh, refresh])

  // Build table + chart data
  const tableRows = stats
    ? Object.entries(stats.summary).map(([proxy, s]) => {
        const attempts = s.success + s.captcha + s.nav_error + s.no_images
        const rate = attempts > 0 ? Math.round((s.success / attempts) * 100) : 0
        return { proxy, ...s, attempts, rate }
      }).sort((a, b) => b.attempts - a.attempts)
    : []

  const chartData = tableRows.map(r => ({
    proxy: r.proxy.length > 12 ? r.proxy.slice(-12) : r.proxy,
    proxyFull: r.proxy,
    success: r.success,
    captcha: r.captcha,
    no_images: r.no_images,
    nav_error: r.nav_error,
  }))

  // Colorize log lines
  function lineClass(line: string) {
    if (line.includes('ERROR') || line.includes('CAPTCHA') || line.includes('captcha')) return 'text-red-400'
    if (line.includes('WARNING') || line.includes('NEWNYM')) return 'text-yellow-400'
    if (line.includes('INFO') && (line.includes('success') || line.includes('downloaded'))) return 'text-green-400'
    if (line.includes('Proxy rotated')) return 'text-blue-400'
    return 'text-gray-300'
  }

  const totalEvents = stats ? Object.values(stats.summary).reduce((a, s) => a + s.success + s.captcha + s.nav_error + s.no_images, 0) : 0
  const totalSuccess = stats ? Object.values(stats.summary).reduce((a, s) => a + s.success, 0) : 0
  const totalCaptchas = stats ? Object.values(stats.summary).reduce((a, s) => a + s.captcha, 0) : 0
  const totalImages = stats ? Object.values(stats.summary).reduce((a, s) => a + s.images_total, 0) : 0

  return (
    <div className="fixed inset-0 z-[2000] bg-black/60 flex items-center justify-center p-4 sm:p-6 backdrop-blur-sm animate-in fade-in">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-h-[90vh] max-w-5xl flex flex-col relative">

        {/* Header */}
        <div className="sticky top-0 bg-white/90 backdrop-blur-md px-6 py-4 border-b border-gray-100 flex items-center justify-between z-10 rounded-t-2xl">
          <div className="flex items-center gap-3">
            <span className="text-2xl">🔍</span>
            <div>
              <h2 className="text-lg font-bold text-gray-900">Google Scraper Status</h2>
              {!loading && (
                <p className="text-xs text-gray-400">
                  {totalEvents} attempts · {totalSuccess} success · {totalCaptchas} captchas · {totalImages} images
                </p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setAutoRefresh(v => !v)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                autoRefresh
                  ? 'bg-green-100 text-green-700 border-green-200'
                  : 'bg-gray-100 text-gray-500 border-gray-200 hover:bg-gray-200'
              }`}
            >
              {autoRefresh ? '⟳ Auto (10s)' : '⟳ Auto off'}
            </button>
            <button
              onClick={refresh}
              disabled={loading}
              className="px-3 py-1.5 rounded-full text-xs font-medium bg-blue-50 text-blue-600 border border-blue-100 hover:bg-blue-100 transition-colors disabled:opacity-50"
            >
              {loading ? 'Loading…' : 'Refresh'}
            </button>
            <button onClick={onClose} className="w-9 h-9 rounded-full bg-gray-100 flex items-center justify-center text-gray-500 hover:bg-gray-200 transition-colors">
              <CloseIcon />
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-gray-100 px-6">
          {(['stats', 'log'] as const).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                tab === t
                  ? 'border-blue-500 text-blue-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {t === 'stats' ? '📊 Proxy Stats' : `📄 Log (last ${logTotal})`}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6">
          {tab === 'stats' && (
            <div className="space-y-6">
              {tableRows.length === 0 ? (
                <div className="text-center py-12 text-gray-400">
                  <div className="text-4xl mb-2">📭</div>
                  <p>No data yet — run the scraper first.</p>
                </div>
              ) : (
                <>
                  {/* Summary table */}
                  <div className="overflow-x-auto rounded-xl border border-gray-100">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="bg-gray-50 text-left">
                          <th className="px-4 py-3 font-semibold text-gray-600">Proxy</th>
                          <th className="px-4 py-3 font-semibold text-gray-600 text-right">Attempts</th>
                          <th className="px-4 py-3 font-semibold text-green-600 text-right">✓ Success</th>
                          <th className="px-4 py-3 font-semibold text-red-500 text-right">🚫 CAPTCHA</th>
                          <th className="px-4 py-3 font-semibold text-amber-500 text-right">∅ No imgs</th>
                          <th className="px-4 py-3 font-semibold text-gray-400 text-right">⚡ Nav err</th>
                          <th className="px-4 py-3 font-semibold text-blue-500 text-right">🖼 Images</th>
                          <th className="px-4 py-3 font-semibold text-gray-600 text-right">Rate</th>
                        </tr>
                      </thead>
                      <tbody>
                        {tableRows.map(r => (
                          <tr key={r.proxy} className="border-t border-gray-100 hover:bg-gray-50">
                            <td className="px-4 py-3 font-mono text-xs text-gray-700 max-w-[180px] truncate" title={r.proxy}>
                              {r.proxy === 'direct' ? '🌐 direct' : r.proxy === 'tor' ? '🧅 tor' : `🔌 ${r.proxy}`}
                            </td>
                            <td className="px-4 py-3 text-right text-gray-500">{r.attempts}</td>
                            <td className="px-4 py-3 text-right font-semibold text-green-600">{r.success}</td>
                            <td className="px-4 py-3 text-right font-semibold text-red-500">{r.captcha}</td>
                            <td className="px-4 py-3 text-right text-amber-500">{r.no_images}</td>
                            <td className="px-4 py-3 text-right text-gray-400">{r.nav_error}</td>
                            <td className="px-4 py-3 text-right font-semibold text-blue-500">{r.images_total}</td>
                            <td className="px-4 py-3 text-right">
                              <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-bold ${
                                r.rate >= 70 ? 'bg-green-100 text-green-700' :
                                r.rate >= 40 ? 'bg-yellow-100 text-yellow-700' :
                                'bg-red-100 text-red-700'
                              }`}>
                                {r.rate}%
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {/* Stacked bar chart */}
                  {chartData.length > 0 && (
                    <div>
                      <h3 className="text-sm font-semibold text-gray-600 mb-3">Outcomes per proxy</h3>
                      <ResponsiveContainer width="100%" height={220}>
                        <BarChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                          <XAxis dataKey="proxy" tick={{ fontSize: 11 }} />
                          <YAxis tick={{ fontSize: 11 }} />
                          <Tooltip
                            formatter={(value, name) => [value, name]}
                            labelFormatter={(label, payload) => payload?.[0]?.payload?.proxyFull ?? label}
                          />
                          <Legend />
                          <Bar dataKey="success"   stackId="a" fill={OUTCOME_COLORS.success}   name="success" />
                          <Bar dataKey="no_images" stackId="a" fill={OUTCOME_COLORS.no_images} name="no images" />
                          <Bar dataKey="nav_error" stackId="a" fill={OUTCOME_COLORS.nav_error} name="nav error" />
                          <Bar dataKey="captcha"   stackId="a" fill={OUTCOME_COLORS.captcha}   name="captcha" radius={[4,4,0,0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}

                  {/* Recent events timeline */}
                  {stats && stats.events.length > 0 && (
                    <div>
                      <h3 className="text-sm font-semibold text-gray-600 mb-3">Recent events (last 30)</h3>
                      <div className="space-y-1 max-h-48 overflow-y-auto">
                        {[...stats.events].reverse().slice(0, 30).map((e, i) => (
                          <div key={i} className="flex items-center gap-2 text-xs py-1 px-3 rounded-lg bg-gray-50">
                            <span className="text-gray-400 w-[140px] shrink-0">{e.ts}</span>
                            <span className={`w-2 h-2 rounded-full shrink-0`} style={{ background: OUTCOME_COLORS[e.outcome] ?? '#ccc' }} />
                            <span className="font-mono text-gray-500 w-[60px] shrink-0">{e.proxy}</span>
                            <span className={`font-semibold w-[70px] shrink-0 ${
                              e.outcome === 'success' ? 'text-green-600' :
                              e.outcome === 'captcha' ? 'text-red-500' :
                              e.outcome === 'no_images' ? 'text-amber-500' : 'text-gray-400'
                            }`}>{e.outcome}</span>
                            <span className="text-gray-400 truncate">{e.cafe_id}</span>
                            {e.images !== undefined && <span className="text-blue-400 shrink-0">+{e.images} imgs</span>}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {tab === 'log' && (
            <div
              ref={logRef}
              className="bg-gray-950 rounded-xl p-4 font-mono text-xs leading-5 overflow-y-auto h-[60vh]"
            >
              {logLines.length === 0 ? (
                <span className="text-gray-500">No log file found.</span>
              ) : (
                logLines.map((line, i) => (
                  <div key={i} className={lineClass(line)}>{line}</div>
                ))
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
