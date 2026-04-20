import { useEffect, useRef, useState, useMemo, useCallback } from 'react'
import { MapContainer, TileLayer, useMapEvents, useMap } from 'react-leaflet'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { CleanCafe, Chain } from './types'
import { PROVIDER_COLORS } from './utils'
import { makePieIcon } from './utils_clean'
import { CleanCafeDetailsPane } from './components/CleanCafeDetailsPane'
import { CafeDetailsPage } from './components/CafeDetailsPage'
import { SettingsModal } from './components/SettingsModal'

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
  cafes: Map<string, CleanCafe>
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

function MarkerLayer({ cafes, onSelect }: MarkerLayerProps) {
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

    for (const cafe of cafes.values()) {
      toRemove.delete(cafe.id)
      if (existing.has(cafe.id)) continue

      const providers = Array.isArray(cafe.providers)
        ? cafe.providers
        : (JSON.parse(cafe.providers as unknown as string ?? '[]') as string[])
      const icon = makePieIcon(providers, 12, cafe.image_count > 0)
      const marker = L.marker([cafe.lat, cafe.lon], { icon })
      marker.on('click', () => onSelect(cafe.id))
      marker.addTo(layer)
      existing.set(cafe.id, marker)
    }

    for (const id of toRemove) {
      existing.get(id)?.remove()
      existing.delete(id)
    }
  }, [cafes, onSelect])

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

  const selectedId = id || null

  useEffect(() => {
    fetch('/api/chains').then(r => r.json()).then(data => setChains(data.slice(0, 30))).catch(console.error)
  }, [])
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
        for (const c of (data.cafes ?? [])) next.set(c.id, c)
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
        <MarkerLayer cafes={cafeMap} onSelect={handleSelect} />
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

      {/* Filter panel */}
      {showFilters && (
        <div className="absolute top-14 left-1/2 -translate-x-1/2 z-[600] bg-white rounded-xl shadow-lg p-4 w-96 max-h-[80vh] overflow-y-auto flex gap-6">
          <div className="flex-1">
            <h3 className="font-semibold mb-3 text-sm">Filters</h3>
            <label className="flex items-center gap-2 mb-2 text-sm cursor-pointer">
              <input type="checkbox" checked={filters.withImages}
                onChange={e => setFilters(f => ({ ...f, withImages: e.target.checked, multipleImages: e.target.checked ? f.multipleImages : false }))} />
              Has images
            </label>
            <label className="flex items-center gap-2 mb-3 text-sm cursor-pointer">
              <input type="checkbox" checked={filters.multipleImages}
                onChange={e => setFilters(f => ({ ...f, multipleImages: e.target.checked, withImages: e.target.checked ? true : f.withImages }))} />
              Multiple images (2+)
            </label>
            <div className="text-xs text-gray-500 mb-2 font-medium">Provider filter</div>
            {Object.entries(PROVIDER_COLORS).map(([p, color]) => (
              <label key={p} className="flex items-center gap-2 mb-1.5 text-sm cursor-pointer">
                <input type="checkbox"
                  checked={filters.providers.has(p)}
                  onChange={e => setFilters(f => {
                    const s = new Set(f.providers)
                    e.target.checked ? s.add(p) : s.delete(p)
                    return { ...f, providers: s }
                  })} />
                <span className="w-2.5 h-2.5 rounded-full" style={{ background: color }} />
                {p}
              </label>
            ))}
            <button className="mt-4 text-xs text-gray-400 hover:text-gray-600"
              onClick={() => setFilters({ withImages: false, multipleImages: false, providers: new Set(), chains: new Set() })}>
              Clear all
            </button>
          </div>
          <div className="flex-1 border-l pl-6">
            <div className="text-xs text-gray-500 mb-2 font-medium">Top Chains</div>
            <div className="space-y-1">
              {chains.map(c => (
                <label key={c.id} className="flex items-center justify-between gap-2 text-sm cursor-pointer">
                  <div className="flex items-center gap-2 truncate">
                    <input type="checkbox"
                      checked={filters.chains.has(c.id)}
                      onChange={e => setFilters(f => {
                        const s = new Set(f.chains)
                        e.target.checked ? s.add(c.id) : s.delete(c.id)
                        return { ...f, chains: s }
                      })} />
                    <span className="truncate" title={c.name}>{c.name}</span>
                  </div>
                  <span className="text-xs text-gray-400">{c.count}</span>
                </label>
              ))}
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
