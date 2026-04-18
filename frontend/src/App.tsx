import { useEffect, useRef, useState, useMemo, useCallback } from 'react'
import { MapContainer, TileLayer, CircleMarker, useMapEvents } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import type { Cafe } from './types'
import { isOpenNow, imageCount, providerColor, hasImage, hasMultipleImages } from './utils'
import { CafeDetailsPane } from './components/CafeDetailsPane'
import { PictureViewerOverlay } from './components/PictureViewerOverlay'
import { StatsModal } from './components/StatsModal'
import { SettingsModal } from './components/SettingsModal'
import { GScraperModal } from './components/GScraperModal'
import './App.css'

interface Filters {
  openNow: boolean
  withImages: boolean
  multipleImages: boolean
  providers: Set<string>
  scrapeDateEnabled: boolean
  maxScrapeDate: number
}

interface FilterStats {
  total: number
  with_images: number
  multiple_images: number
  open_now: number
  providers: { name: string; count: number }[]
  min_scrape_date: string
  max_scrape_date: string
}

const LS_KEY_PREFIX = 'workcafe_cafes_v2'

function filterCacheKey(filters: Filters): string {
  const parts: string[] = []
  if (filters.openNow) parts.push('on')
  if (filters.multipleImages) parts.push('mi')
  else if (filters.withImages) parts.push('wi')
  if (filters.providers.size > 0) parts.push('p:' + [...filters.providers].sort().join(','))
  if (filters.scrapeDateEnabled) parts.push('sd:' + Math.floor(filters.maxScrapeDate / (1000 * 60 * 60)))
  return parts.length > 0 ? LS_KEY_PREFIX + ':' + parts.join('|') : LS_KEY_PREFIX
}

function loadCacheFromLS(key: string): Map<string, Cafe> {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return new Map()
    const arr: Cafe[] = JSON.parse(raw)
    return new Map(arr.map(c => [c.id, c]))
  } catch { return new Map() }
}

function saveCacheToLS(map: Map<string, Cafe>, key: string) {
  try {
    localStorage.setItem(key, JSON.stringify([...map.values()]))
  } catch {}
}

function clearAllCaches() {
  const toRemove: string[] = []
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i)
    if (k?.startsWith(LS_KEY_PREFIX) || k === 'workcafe_cafes_v1') toRemove.push(k)
  }
  toRemove.forEach(k => localStorage.removeItem(k))
}

interface ViewportBounds {
  minLat: number; maxLat: number; minLon: number; maxLon: number
}


function ViewportTracker({ onBoundsChange }: { onBoundsChange: (b: ViewportBounds) => void }) {
  const map = useMapEvents({
    moveend: () => {
      const b = map.getBounds()
      onBoundsChange({ minLat: b.getSouth(), maxLat: b.getNorth(), minLon: b.getWest(), maxLon: b.getEast() })
    },
    zoomend: () => {
      const b = map.getBounds()
      onBoundsChange({ minLat: b.getSouth(), maxLat: b.getNorth(), minLon: b.getWest(), maxLon: b.getEast() })
    },
  })
  return null
}

export default function App() {
  const initialFilters: Filters = { openNow: false, withImages: false, multipleImages: false, providers: new Set(), scrapeDateEnabled: false, maxScrapeDate: Date.now() }
  const [cafeMap, setCafeMap] = useState<Map<string, Cafe>>(() => loadCacheFromLS(filterCacheKey(initialFilters)))
  const [search, setSearch] = useState('')
  const hasLSData = useMemo(() => cafeMap.size > 0, []) // eslint-disable-line react-hooks/exhaustive-deps
  const [loading, setLoading] = useState(!hasLSData)
  const [showFilters, setShowFilters] = useState(false)
  const [showStats, setShowStats] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showGScraper, setShowGScraper] = useState(false)
  const [filters, setFilters] = useState<Filters>(initialFilters)
  const [filterStats, setFilterStats] = useState<FilterStats | null>(null)
  const filterRef = useRef<HTMLDivElement>(null)
  const [viewportTotal, setViewportTotal] = useState<number | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const boundsRef = useRef<ViewportBounds | null>(null)
  const filtersRef = useRef<Filters>(filters)
  filtersRef.current = filters

  const [selectedCafe, setSelectedCafe] = useState<Cafe | null>(null)
  const [fullScreenImageIndex, setFullScreenImageIndex] = useState<number | null>(null)

  const cafes = useMemo(() => [...cafeMap.values()], [cafeMap])

  // Fetch global filter stats from DB once on mount
  useEffect(() => {
    fetch('/api/filter-stats')
      .then(r => r.json())
      .then((data: FilterStats) => {
        setFilterStats(data)
        // Init maxScrapeDate from DB max
        if (data.max_scrape_date) {
          const ts = new Date(data.max_scrape_date).getTime()
          if (!isNaN(ts)) setFilters(f => ({ ...f, maxScrapeDate: ts }))
        }
      })
      .catch(() => {})
  }, [])

  const fetchViewport = useCallback((bounds: ViewportBounds, activeFilters: Filters) => {
    setLoading(true)
    const p = new URLSearchParams({
      minLat: String(bounds.minLat), maxLat: String(bounds.maxLat),
      minLon: String(bounds.minLon), maxLon: String(bounds.maxLon),
    })
    if (activeFilters.openNow) p.set('openNow', 'true')
    if (activeFilters.multipleImages) p.set('multipleImages', 'true')
    else if (activeFilters.withImages) p.set('withImages', 'true')
    if (activeFilters.providers.size > 0) p.set('providers', [...activeFilters.providers].join(','))
    if (activeFilters.scrapeDateEnabled) p.set('maxScrapeDate', String(activeFilters.maxScrapeDate))

    const cacheKey = filterCacheKey(activeFilters)

    fetch(`/api/cafes?${p}`)
      .then(r => r.json())
      .then((data: { cafes: Cafe[]; showing: number; total: number }) => {
        if (cacheKey !== filterCacheKey(filtersRef.current)) {
          // If the filter has changed since this fetch started, update the old cache in LS but don't pollute the current state
          const oldCache = loadCacheFromLS(cacheKey)
          for (const c of data.cafes) oldCache.set(c.id, c)
          saveCacheToLS(oldCache, cacheKey)
          return
        }
        setViewportTotal(data.total)
        setCafeMap(prev => {
          const next = new Map(prev)
          for (const c of data.cafes) next.set(c.id, c)
          saveCacheToLS(next, cacheKey)
          return next
        })
        setLoading(false)
      })
      .catch(() => {
        if (cacheKey === filterCacheKey(filtersRef.current)) setLoading(false)
      })
  }, [])

  const handleBoundsChange = useCallback((bounds: ViewportBounds) => {
    boundsRef.current = bounds
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => fetchViewport(bounds, filtersRef.current), 400)
  }, [fetchViewport])

  // Initial load with default Seoul viewport — skip if LS already has data
  useEffect(() => {
    if (hasLSData) return
    fetchViewport({ minLat: 37.52, maxLat: 37.61, minLon: 126.93, maxLon: 127.03 }, filters)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // On filter change: switch to that filter's LS cache, then re-fetch
  useEffect(() => {
    if (!boundsRef.current) return
    setCafeMap(loadCacheFromLS(filterCacheKey(filters)))
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => fetchViewport(boundsRef.current!, filtersRef.current), 200)
  }, [filters, fetchViewport]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (filterRef.current && !filterRef.current.contains(e.target as Node)) {
        setShowFilters(false)
      }
    }
    if (showFilters) document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [showFilters])

  // Scrape date range from filter-stats; fall back to computing from cafeMap
  const minScrapeDate = useMemo(() => {
    if (filterStats?.min_scrape_date) return new Date(filterStats.min_scrape_date).getTime() || Date.now()
    if (cafeMap.size === 0) return Date.now()
    return Math.min(...[...cafeMap.values()].map(c => new Date(c.scraped_at).getTime() || Date.now()))
  }, [filterStats, cafeMap])

  const maxScrapeDateTotal = useMemo(() => {
    if (filterStats?.max_scrape_date) return new Date(filterStats.max_scrape_date).getTime() || Date.now()
    if (cafeMap.size === 0) return Date.now()
    return Math.max(...[...cafeMap.values()].map(c => new Date(c.scraped_at).getTime() || Date.now()))
  }, [filterStats, cafeMap])

  // Client-side filter: search and all active filters
  const filtered = cafes.filter(c => {
    if (search) {
      const q = search.toLowerCase()
      if (!c.name.toLowerCase().includes(q) && !c.address.toLowerCase().includes(q)) return false
    }
    if (filters.openNow && !isOpenNow(c)) return false
    if (filters.multipleImages && !hasMultipleImages(c)) return false
    else if (filters.withImages && !hasImage(c)) return false
    if (filters.providers.size > 0 && !filters.providers.has(c.provider)) return false
    if (filters.scrapeDateEnabled) {
      const scrapeTime = new Date(c.scraped_at).getTime()
      if (scrapeTime > filters.maxScrapeDate) return false
    }
    return true
  })

  // openNow still computed client-side (time-dependent, naver-only)
  const filteredOpenNow = useMemo(() => filtered.filter(isOpenNow), [filtered])

  const availableProviders = filterStats?.providers?.map(p => p.name) ?? [...new Set(cafes.map(c => c.provider))]

  function toggleProvider(p: string) {
    setFilters(f => {
      const next = new Set(f.providers)
      if (next.has(p)) next.delete(p)
      else next.add(p)
      return { ...f, providers: next }
    })
  }

  const activeFilterCount = (filters.openNow ? 1 : 0) + (filters.withImages ? 1 : 0) + (filters.multipleImages ? 1 : 0) + filters.providers.size + (filters.scrapeDateEnabled ? 1 : 0)

  return (
    <div className="relative w-screen h-screen overflow-hidden">
      {/* Fullscreen map */}
      <MapContainer
        center={[37.5665, 126.978]}
        zoom={14}
        className="w-full h-full"
        zoomControl={false}
      >
        <TileLayer
          attribution='&copy; <a href="https://carto.com/">CARTO</a>'
          url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
          subdomains="abcd"
          maxZoom={20}
        />
        <ViewportTracker onBoundsChange={handleBoundsChange} />
        {filtered.map(c => {
          const isSelected = selectedCafe?.id === c.id;
          return (
            <CircleMarker
              key={c.id}
              center={[c.lat, c.lon]}
              radius={isSelected ? 10 : 7}
              pathOptions={{
                color: isSelected ? '#000' : '#fff',
                weight: isSelected ? 3 : 2,
                fillColor: providerColor(c.provider),
                fillOpacity: isSelected ? 1 : 0.88,
              }}
              eventHandlers={{
                click: () => {
                  setSelectedCafe(c)
                  setFullScreenImageIndex(null)
                }
              }}
            />
          );
        })}
      </MapContainer>

      {/* Left slide-in details pane */}
      {selectedCafe && (
        <CafeDetailsPane
          cafe={selectedCafe}
          onClose={() => setSelectedCafe(null)}
          onFullScreenImage={(index) => setFullScreenImageIndex(index)}
        />
      )}

      {/* Full screen picture viewer overlay */}
      {selectedCafe && fullScreenImageIndex !== null && (
        <PictureViewerOverlay
          cafe={selectedCafe}
          initialIndex={fullScreenImageIndex}
          onClose={() => setFullScreenImageIndex(null)}
        />
      )}

      {/* Search bar overlay */}
      <div
        ref={filterRef}
        className={`search-container ${selectedCafe ? 'sidebar-open' : ''}`}
      >
        <div className="search-pill">
          {loading && <div className="search-loading-bar" />}

          {loading ? (
            <svg className="search-icon spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-20" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
              <path className="opacity-80" fill="currentColor" d="M4 12a8 8 0 018-8v3a5 5 0 00-5 5H4z" />
            </svg>
          ) : (
            <svg className="search-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M17 11A6 6 0 1 1 5 11a6 6 0 0 1 12 0z" />
            </svg>
          )}

          <input
            type="search"
            placeholder={loading ? 'Loading cafes…' : 'Search work cafes in Seoul…'}
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="search-input"
          />

          {!loading && (
            <span className="search-count">
              {filtered.length.toLocaleString()}{viewportTotal !== null && viewportTotal > filtered.length ? ` / ${viewportTotal.toLocaleString()}` : ''} cafes
              {imageCount(filtered) > 0 && <span className="search-count-img"> · 📷 {imageCount(filtered).toLocaleString()}</span>}
            </span>
          )}

          <div className="search-divider" />

          <button
            onClick={() => setShowFilters(v => !v)}
            className={`filter-btn ${showFilters || activeFilterCount > 0 ? 'active' : ''}`}
          >
            <svg width="15" height="15" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 4h18M7 9h10M11 14h2" />
            </svg>
            Filters
            {activeFilterCount > 0 && (
              <span className="filter-badge">{activeFilterCount}</span>
            )}
          </button>
        </div>

        {/* Filter panel */}
        {showFilters && (
          <div className="filter-panel">
            <div className="filter-panel-header">
              <span>Filters</span>
              {activeFilterCount > 0 && (
                <button
                  onClick={() => setFilters(f => ({ ...f, openNow: false, withImages: false, multipleImages: false, providers: new Set(), scrapeDateEnabled: false, maxScrapeDate: maxScrapeDateTotal }))}
                  className="filter-clear"
                >
                  Clear all
                </button>
              )}
            </div>

            <label className="toggle-row">
              <div
                onClick={() => setFilters(f => ({ ...f, openNow: !f.openNow }))}
                className={`toggle ${filters.openNow ? 'on' : ''}`}
              >
                <div className="toggle-knob" />
              </div>
              <span>Open now <span className="toggle-hint">
                {filters.openNow
                  ? `(${filteredOpenNow.length.toLocaleString()} in view)`
                  : filterStats ? `(${filterStats.open_now.toLocaleString()})` : ''}
              </span></span>
            </label>

            <label className="toggle-row">
              <div
                onClick={() => setFilters(f => ({ ...f, withImages: !f.withImages, multipleImages: false }))}
                className={`toggle ${filters.withImages ? 'on' : ''}`}
              >
                <div className="toggle-knob" />
              </div>
              <span>With photos <span className="toggle-hint">
                {filterStats ? `(${filterStats.with_images.toLocaleString()})` : ''}
              </span></span>
            </label>

            <label className="toggle-row">
              <div
                onClick={() => setFilters(f => ({ ...f, multipleImages: !f.multipleImages, withImages: false }))}
                className={`toggle ${filters.multipleImages ? 'on' : ''}`}
              >
                <div className="toggle-knob" />
              </div>
              <span>Multiple photos <span className="toggle-hint">
                {filterStats ? `(${filterStats.multiple_images.toLocaleString()})` : ''}
              </span></span>
            </label>

            {availableProviders.length > 1 && (
              <div className="filter-section">
                <div className="filter-section-label">Source</div>
                <div className="filter-chips">
                  {availableProviders.map(p => {
                    const count = filterStats?.providers.find(pr => pr.name === p)?.count
                    return (
                      <button
                        key={p}
                        onClick={() => toggleProvider(p)}
                        className={`chip ${filters.providers.has(p) ? 'active' : ''}`}
                        style={filters.providers.has(p) ? { background: providerColor(p), borderColor: providerColor(p) } : {}}
                      >
                        <span className="chip-dot" style={{ background: providerColor(p) }} />
                        {p} <span style={{ opacity: 0.8, marginLeft: '4px' }}>{count != null ? `(${count.toLocaleString()})` : ''}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            <div className="filter-section" style={{ marginTop: '16px' }}>
              <label className="toggle-row" style={{ marginBottom: '8px' }}>
                <div
                  onClick={() => setFilters(f => ({ ...f, scrapeDateEnabled: !f.scrapeDateEnabled }))}
                  className={`toggle ${filters.scrapeDateEnabled ? 'on' : ''}`}
                >
                  <div className="toggle-knob" />
                </div>
                <span>Scraped before</span>
              </label>
              {filters.scrapeDateEnabled && (
                <div style={{ padding: '0 8px', marginBottom: '8px' }}>
                  <input
                    type="range"
                    min={minScrapeDate}
                    max={maxScrapeDateTotal}
                    step={1000 * 60 * 60}
                    value={filters.maxScrapeDate}
                    onChange={e => setFilters(f => ({ ...f, maxScrapeDate: Number(e.target.value) }))}
                    style={{ width: '100%', accentColor: '#7c3aed' }}
                  />
                  <div style={{ fontSize: '12px', color: '#6b7280', textAlign: 'center', marginTop: '4px' }}>
                    {new Date(filters.maxScrapeDate).toLocaleString()}
                  </div>
                </div>
              )}
            </div>

            <div className="filter-section" style={{ marginTop: '16px', paddingTop: '12px', borderTop: '1px solid #f3f4f6' }}>
              <button
                onClick={() => {
                  clearAllCaches()
                  setCafeMap(new Map())
                }}
                className="filter-clear"
                style={{ width: '100%', textAlign: 'center', padding: '6px 0', color: '#ef4444' }}
              >
                Clear local cache
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Load more button */}
      {viewportTotal !== null && viewportTotal > 1000 && (
        <div className="absolute bottom-6 left-1/2 -translate-x-1/2 z-[1000]">
          <button
            onClick={() => {
              if (debounceRef.current) clearTimeout(debounceRef.current)
            }}
            className="bg-white/90 backdrop-blur-md px-4 py-2 rounded-full shadow-md text-sm font-semibold text-gray-700 border border-gray-200"
          >
            Showing 1000 of {viewportTotal.toLocaleString()} — zoom in to see more
          </button>
        </div>
      )}

      {/* Branding badge */}
      <div className="brand-badge">
        ☕ WorkCafe Seoul
      </div>

      {/* Top-right buttons */}
      <div className="absolute top-24 right-4 sm:top-6 sm:right-6 z-[1000] flex flex-col sm:flex-row items-end sm:items-center gap-4">
        <button
          onClick={() => setShowStats(true)}
          className="bg-white/90 backdrop-blur-md px-4 py-2 rounded-full shadow-md text-sm font-semibold text-gray-700 hover:text-purple-600 hover:bg-white transition-colors border border-gray-100 flex items-center gap-2"
        >
          <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
          STATS
        </button>
        <button
          onClick={() => setShowGScraper(true)}
          className="bg-white/90 backdrop-blur-md px-4 py-2 rounded-full shadow-md text-sm font-semibold text-gray-700 hover:text-green-600 hover:bg-white transition-colors border border-gray-100 flex items-center gap-2"
        >
          🔍 GScraper
        </button>
        <button
          onClick={() => setShowSettings(true)}
          className="bg-white/90 backdrop-blur-md px-4 py-2 rounded-full shadow-md text-sm font-semibold text-gray-700 hover:text-blue-600 hover:bg-white transition-colors border border-gray-100 flex items-center gap-2"
        >
          <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
          SCRAPERS
        </button>
      </div>

      {/* Stats Modal */}
      {showStats && <StatsModal cafes={cafes} onClose={() => setShowStats(false)} />}

      {/* Settings Modal */}
      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}

      {/* GScraper Modal */}
      {showGScraper && <GScraperModal onClose={() => setShowGScraper(false)} />}
    </div>
  )
}
