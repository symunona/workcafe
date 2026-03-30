import { useEffect, useRef, useState } from 'react'
import { MapContainer, TileLayer, CircleMarker } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import type { Cafe } from './types'
import { isOpenNow, hasImage, hasMultipleImages, imageCount, multiImgCount, providerColor } from './utils'
import { CafeDetailsPane } from './components/CafeDetailsPane'
import { PictureViewerOverlay } from './components/PictureViewerOverlay'
import { StatsModal } from './components/StatsModal'
import './App.css'

interface Filters {
  openNow: boolean
  withImages: boolean
  multipleImages: boolean
  providers: Set<string>
}

export default function App() {
  const [cafes, setCafes] = useState<Cafe[]>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [showFilters, setShowFilters] = useState(false)
  const [showStats, setShowStats] = useState(false)
  const [filters, setFilters] = useState<Filters>({ openNow: false, withImages: false, multipleImages: false, providers: new Set() })
  const filterRef = useRef<HTMLDivElement>(null)

  const [selectedCafe, setSelectedCafe] = useState<Cafe | null>(null)
  const [fullScreenImageIndex, setFullScreenImageIndex] = useState<number | null>(null)

  useEffect(() => {
    fetch('/api/cafes')
      .then(r => r.json())
      .then((data: Cafe[]) => { setCafes(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (filterRef.current && !filterRef.current.contains(e.target as Node)) {
        setShowFilters(false)
      }
    }
    if (showFilters) document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [showFilters])

  const filtered = cafes.filter(c => {
    if (search) {
      const q = search.toLowerCase()
      if (!c.name.toLowerCase().includes(q) && !c.address.toLowerCase().includes(q)) return false
    }
    if (filters.openNow && !isOpenNow(c)) return false
    if (filters.withImages && !hasImage(c)) return false
    if (filters.multipleImages && !hasMultipleImages(c)) return false
    if (filters.providers.size > 0 && !filters.providers.has(c.provider)) return false
    return true
  })

  const availableProviders = [...new Set(cafes.map(c => c.provider))]

  function toggleProvider(p: string) {
    setFilters(f => {
      const next = new Set(f.providers)
      if (next.has(p)) next.delete(p)
      else next.add(p)
      return { ...f, providers: next }
    })
  }

  const activeFilterCount = (filters.openNow ? 1 : 0) + (filters.withImages ? 1 : 0) + (filters.multipleImages ? 1 : 0) + filters.providers.size
  const imgCount = imageCount(cafes)
  const multiImgCountVal = multiImgCount(cafes)

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
        {filtered.map(c => (
          <CircleMarker
            key={c.id}
            center={[c.lat, c.lon]}
            radius={7}
            pathOptions={{
              color: '#fff',
              weight: 2,
              fillColor: providerColor(c.provider),
              fillOpacity: 0.88,
            }}
            eventHandlers={{
              click: () => {
                setSelectedCafe(c)
                setFullScreenImageIndex(null)
              }
            }}
          />
        ))}
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
        className="absolute top-5 left-1/2 -translate-x-1/2 z-[1000] w-full max-w-xl px-4"
        style={{ 
          transition: 'transform 0.3s ease', 
          transform: selectedCafe ? (window.innerWidth > 768 ? 'translate(calc(-50% + 200px), 0)' : 'translate(-50%, 0)') : 'translate(-50%, 0)' 
        }}
      >
        {/* Pill */}
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
              {filtered.length.toLocaleString()} cafes
              {imgCount > 0 && <span className="search-count-img"> · 📷 {imageCount(filtered).toLocaleString()}</span>}
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
                  onClick={() => setFilters({ openNow: false, withImages: false, multipleImages: false, providers: new Set() })}
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
              <span>Open now <span className="toggle-hint">({cafes.filter(isOpenNow).length.toLocaleString()})</span></span>
            </label>

            <label className="toggle-row">
              <div
                onClick={() => setFilters(f => ({ ...f, withImages: !f.withImages }))}
                className={`toggle ${filters.withImages ? 'on' : ''}`}
              >
                <div className="toggle-knob" />
              </div>
              <span>With photos <span className="toggle-hint">({imgCount.toLocaleString()})</span></span>
            </label>

            <label className="toggle-row">
              <div
                onClick={() => setFilters(f => ({ ...f, multipleImages: !f.multipleImages }))}
                className={`toggle ${filters.multipleImages ? 'on' : ''}`}
              >
                <div className="toggle-knob" />
              </div>
              <span>Multiple photos <span className="toggle-hint">({multiImgCountVal.toLocaleString()})</span></span>
            </label>

            {availableProviders.length > 1 && (
              <div className="filter-section">
                <div className="filter-section-label">Source</div>
                <div className="filter-chips">
                  {availableProviders.map(p => {
                    const count = cafes.filter(c => c.provider === p).length;
                    return (
                      <button
                        key={p}
                        onClick={() => toggleProvider(p)}
                        className={`chip ${filters.providers.has(p) ? 'active' : ''}`}
                        style={filters.providers.has(p) ? { background: providerColor(p), borderColor: providerColor(p) } : {}}
                      >
                        <span className="chip-dot" style={{ background: providerColor(p) }} />
                        {p} <span style={{ opacity: 0.8, marginLeft: '4px' }}>({count})</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Branding badge */}
      <div className="brand-badge">
        ☕ WorkCafe Seoul
      </div>

      {/* Stats Button */}
      <button 
        onClick={() => setShowStats(true)}
        className="absolute top-5 right-5 z-[1000] bg-white/90 backdrop-blur-md px-4 py-2 rounded-full shadow-md text-sm font-semibold text-gray-700 hover:text-purple-600 hover:bg-white transition-colors border border-gray-100 flex items-center gap-2"
      >
        <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
        </svg>
        STATS
      </button>

      {/* Stats Modal */}
      {showStats && <StatsModal cafes={cafes} onClose={() => setShowStats(false)} />}
    </div>
  )
}
