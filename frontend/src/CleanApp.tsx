import { useEffect, useRef, useState, useMemo, useCallback } from 'react'
import { MapContainer, TileLayer, useMapEvents, useMap } from 'react-leaflet'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { CleanCafe, Chain } from './types'
import { PROVIDER_COLORS } from './utils'
import { makePieIcon, CHAIN_COLORS } from './utils_clean'
import { CleanCafeDetailsPane } from './components/CleanCafeDetailsPane'
import { CafeDetailsPage } from './components/CafeDetailsPage'
import { SettingsModal } from './components/SettingsModal'
import { Checkbox } from './components/Checkbox'

interface ViewportBounds {
  minLat: number; maxLat: number; minLon: number; maxLon: number
}

interface Filters {
  withImages: boolean
  multipleImages: boolean
  providers: Set<string>
  chains: Set<string>
}

interface MarkerLayerProps {
  scraped_cafes: Map<string, CleanCafe>
  onSelect: (id: string) => void
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
    load: () => {
      const b = map.getBounds()
      onBoundsChange({ minLat: b.getSouth(), maxLat: b.getNorth(), minLon: b.getWest(), maxLon: b.getEast() })
    },
  })
  // Fire initial bounds after mount
  useEffect(() => {
    const b = map.getBounds()
    onBoundsChange({ minLat: b.getSouth(), maxLat: b.getNorth(), minLon: b.getWest(), maxLon: b.getEast() })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return null
}

function MapPanner({ target }: { target: [number, number] | null }) {
  const map = useMap()
  useEffect(() => {
    if (target) map.setView(target, Math.max(map.getZoom(), 16), { animate: true })
  }, [target, map])
  return null
}

function MarkerLayer({ scraped_cafes, onSelect }: MarkerLayerProps) {
  const map = useMap()
  const markersRef = useRef<Map<string, L.Marker>>(new Map())
  const layerRef = useRef<L.LayerGroup | null>(null)

  useEffect(() => {
    if (!layerRef.current) {
      layerRef.current = L.layerGroup().addTo(map)
    }
  }, [map])

  useEffect(() => {
    const layer = layerRef.current
    if (!layer) return

    const existing = markersRef.current
    const toRemove = new Set(existing.keys())

    for (const cafe of scraped_cafes.values()) {
      toRemove.delete(cafe.id)
      if (existing.has(cafe.id)) continue

      const providers = Array.isArray(cafe.providers)
        ? cafe.providers
        : (JSON.parse(cafe.providers as unknown as string ?? '[]') as string[])
      const icon = makePieIcon(providers, 14, cafe.image_count > 0, cafe.chain_name_english || cafe.chain_name)
      const marker = L.marker([cafe.lat, cafe.lon], { icon })
      marker.on('click', () => onSelect(cafe.id))
      marker.addTo(layer)
      existing.set(cafe.id, marker)
    }

    for (const id of toRemove) {
      existing.get(id)?.remove()
      existing.delete(id)
    }
  }, [scraped_cafes, onSelect])

  return null
}

export default function CleanApp() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const [cafeMap, setCafeMap] = useState<Map<string, CleanCafe>>(new Map())
  const [loading, setLoading] = useState(true)
  const [filters, setFilters] = useState<Filters>({ withImages: false, multipleImages: false, providers: new Set(), chains: new Set() })
  const [showFilters, setShowFilters] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [chains, setChains] = useState<Chain[]>([])
  const [total, setTotal] = useState(0)
  const [isLocating, setIsLocating] = useState(false)

  const selectedId = id || null
  const [mapTarget, setMapTarget] = useState<[number, number] | null>(null)

  useEffect(() => {
    fetch('/api/chains').then(r => r.json()).then(data => setChains(data.slice(0, 30))).catch(console.error)
  }, [])

  // Pan map to cafe when navigating directly to a /cafe/:id URL
  useEffect(() => {
    if (!selectedId) return
    fetch(`/api/clean_cafe?id=${encodeURIComponent(selectedId)}`)
      .then(r => r.json())
      .then(data => { if (data?.lat && data?.lon) setMapTarget([data.lat, data.lon]) })
      .catch(() => {})
  }, [selectedId])
  const abortRef = useRef<AbortController | null>(null)
  const boundsRef = useRef<ViewportBounds | null>(null)

  const fetchCafes = useCallback(async (bounds: ViewportBounds, f: Filters) => {
    if (abortRef.current) abortRef.current.abort()
    abortRef.current = new AbortController()

    const params = new URLSearchParams({
      minLat: bounds.minLat.toString(),
      maxLat: bounds.maxLat.toString(),
      minLon: bounds.minLon.toString(),
      maxLon: bounds.maxLon.toString(),
    })
    if (f.multipleImages) params.set('multipleImages', 'true')
    else if (f.withImages) params.set('withImages', 'true')
    if (f.providers.size > 0) params.set('providers', [...f.providers].join(','))
    if (f.chains.size > 0) params.set('chains', [...f.chains].join(','))

    setLoading(true)
    try {
      const res = await fetch(`/api/clean_cafes?${params}`, { signal: abortRef.current.signal })
      const data = await res.json()
      setTotal(data.total ?? 0)
      setCafeMap(prev => {
        const next = new Map(prev)
        for (const c of (data.scraped_cafes ?? [])) next.set(c.id, c)
        return next
      })
    } catch (e) {
      if ((e as Error).name !== 'AbortError') console.error(e)
    } finally {
      setLoading(false)
    }
  }, [])

  const handleBoundsChange = useCallback((b: ViewportBounds) => {
    boundsRef.current = b
    fetchCafes(b, filters)
  }, [fetchCafes, filters])

  // Re-fetch when filters change
  useEffect(() => {
    if (boundsRef.current) {
      setCafeMap(new Map())
      fetchCafes(boundsRef.current, filters)
    }
  }, [filters]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSelect = useCallback((cid: string) => navigate(`/cafe/${cid}`), [navigate])

  const cafesInView = useMemo(() => cafeMap.size, [cafeMap])

  const handleGPSClick = useCallback(() => {
    if ('geolocation' in navigator) {
      setIsLocating(true)
      navigator.geolocation.getCurrentPosition(
        (position) => {
          setIsLocating(false)
          setMapTarget([position.coords.latitude, position.coords.longitude])
        },
        (error) => {
          setIsLocating(false)
          console.error('Error getting location:', error)
          alert('Unable to retrieve your location')
        },
        { enableHighAccuracy: true, timeout: 5000, maximumAge: 0 }
      )
    } else {
      alert('Geolocation is not supported by your browser')
    }
  }, [])

  return (
    <div className="relative w-screen h-screen">
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
        <MapPanner target={mapTarget} />
        <MarkerLayer scraped_cafes={cafeMap} onSelect={handleSelect} />
      </MapContainer>

      {/* Top bar */}
      <div className="absolute top-2 left-1/2 -translate-x-1/2 z-[500] flex items-center gap-2">
        <div className="bg-white rounded-lg shadow px-3 py-1.5 text-sm font-medium flex items-center gap-2">
          <span className="text-gray-700">☕ Workcafe</span>
          <span className="text-gray-400">|</span>
          <span className="text-gray-500 text-xs">
            {loading ? '…' : `${cafesInView.toLocaleString()} / ${total.toLocaleString()}`}
          </span>
        </div>
        <button
          className={`bg-white rounded-lg shadow px-3 py-1.5 text-sm hover:bg-gray-50 ${filters.withImages || filters.multipleImages || filters.providers.size > 0 ? 'ring-2 ring-blue-400' : ''}`}
          onClick={() => setShowFilters(!showFilters)}
        >
          Filter {filters.withImages || filters.multipleImages || filters.providers.size > 0 ? '●' : ''}
        </button>
        <button
          className="bg-white rounded-lg shadow px-3 py-1.5 text-sm hover:bg-gray-50 text-gray-500"
          onClick={() => setShowSettings(true)}
        >
          Scraper Status
        </button>
      </div>

      {/* Legend */}
      <div className="absolute bottom-6 left-2 z-[500] bg-white/90 rounded-lg shadow p-2 text-xs">
        <div className="text-gray-500 mb-1 font-medium">Providers</div>
        {Object.entries(PROVIDER_COLORS).map(([p, color]) => (
          <div key={p} className="flex items-center gap-1.5 py-0.5">
            <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: color }} />
            <span className="text-gray-600">{p}</span>
          </div>
        ))}
        <div className="mt-1 pt-1 border-t text-gray-400">black ring = has images</div>
      </div>

      {/* GPS Button */}
      <button
        onClick={handleGPSClick}
        disabled={isLocating}
        className={`absolute bottom-6 right-6 z-[500] bg-white rounded-full shadow-lg p-3 transition-colors flex items-center justify-center ${isLocating ? 'text-blue-500 bg-blue-50' : 'text-gray-700 hover:bg-gray-50'}`}
        title="Go to my location"
      >
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className={`w-6 h-6 ${isLocating ? 'animate-pulse' : ''}`}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 2.25v2.25m0 15v2.25M2.25 12h2.25m15 0h2.25M5.25 12a6.75 6.75 0 1 1 13.5 0 6.75 6.75 0 0 1-13.5 0Z" />
        </svg>
      </button>

      {/* Filter panel */}
      {showFilters && (
        <div className="absolute top-14 left-1/2 -translate-x-1/2 z-[600] bg-white rounded-xl shadow-2xl p-6 w-[500px] max-h-[80vh] overflow-y-auto flex gap-8">
          <div className="flex-1">
            <h3 className="font-semibold mb-4 text-base">Filters</h3>
            <div className="mb-3 hover:bg-gray-50 p-1.5 -ml-1.5 rounded-lg transition-colors">
              <Checkbox
                checked={filters.withImages}
                onChange={v => setFilters(f => ({ ...f, withImages: v, multipleImages: v ? f.multipleImages : false }))}
                label={<span className="text-sm">Has images</span>}
              />
            </div>
            <div className="mb-4 hover:bg-gray-50 p-1.5 -ml-1.5 rounded-lg transition-colors">
              <Checkbox
                checked={filters.multipleImages}
                onChange={v => setFilters(f => ({ ...f, multipleImages: v, withImages: v ? true : f.withImages }))}
                label={<span className="text-sm">Multiple images (2+)</span>}
              />
            </div>
            <div className="text-xs text-gray-500 mb-3 font-semibold uppercase tracking-wider">Provider filter</div>
            {Object.entries(PROVIDER_COLORS).map(([p, color]) => (
              <div key={p} className="mb-2 hover:bg-gray-50 p-1.5 -ml-1.5 rounded-lg transition-colors">
                <Checkbox
                  checked={filters.providers.has(p)}
                  onChange={v => setFilters(f => {
                    const s = new Set(f.providers)
                    v ? s.add(p) : s.delete(p)
                    return { ...f, providers: s }
                  })}
                  label={
                    <span className="flex items-center gap-2 text-sm">
                      <span className="w-3 h-3 rounded-full shadow-sm flex-shrink-0" style={{ background: color }} />
                      {p}
                    </span>
                  }
                />
              </div>
            ))}
            <button className="mt-6 text-sm font-medium text-gray-400 hover:text-gray-800 transition-colors"
              onClick={() => setFilters({ withImages: false, multipleImages: false, providers: new Set(), chains: new Set() })}>
              Clear all filters
            </button>
          </div>
          <div className="flex-1">
            <div className="text-xs text-gray-500 mb-3 font-semibold uppercase tracking-wider">Top Chains</div>
            <div className="space-y-1.5">
              {chains.map(c => {
                const chainColor = CHAIN_COLORS[c.name_english || c.name];
                return (
                <div key={c.id} className="hover:bg-gray-50 p-1.5 -ml-1.5 rounded-lg transition-colors">
                  <Checkbox
                    checked={filters.chains.has(c.id)}
                    onChange={v => setFilters(f => {
                      const s = new Set(f.chains)
                      v ? s.add(c.id) : s.delete(c.id)
                      return { ...f, chains: s }
                    })}
                    label={
                      <span className="flex items-center justify-between gap-2 text-sm w-full">
                        <span className="flex items-center gap-2 truncate">
                          {chainColor && <span className="w-3 h-3 rounded-full shadow-sm flex-shrink-0" style={{ background: chainColor }} />}
                          <span className="truncate" title={c.name_english || c.name}>{c.name_english || c.name}</span>
                        </span>
                        <span className="text-xs font-medium text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full flex-shrink-0">{c.count}</span>
                      </span>
                    }
                  />
                </div>
              )})}
            </div>
          </div>
        </div>
      )}

      {selectedId && (
        <CleanCafeDetailsPane cafeId={selectedId} onClose={() => navigate('/')} />
      )}
      
      {selectedId && location.search && (
        <CafeDetailsPage />
      )}
      
      {showSettings && (
        <SettingsModal onClose={() => setShowSettings(false)} />
      )}
    </div>
  )
}
