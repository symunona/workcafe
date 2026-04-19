import { useEffect, useRef, useState, useMemo, useCallback } from 'react'
import { MapContainer, TileLayer, useMapEvents } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { CleanCafe } from './types'
import { PROVIDER_COLORS } from './utils'
import { makePieIcon } from './utils_clean'
import { CleanCafeDetailsPane } from './components/CleanCafeDetailsPane'

interface ViewportBounds {
  minLat: number; maxLat: number; minLon: number; maxLon: number
}

interface Filters {
  withImages: boolean
  multipleImages: boolean
  providers: Set<string>
}

const API_BASE = '/api/clean_cafes'
const SCRAPER_URL = '/scraper/'

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

export default function CleanApp() {
  const [cafeMap, setCafeMap] = useState<Map<string, CleanCafe>>(new Map())
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [viewport, setViewport] = useState<ViewportBounds | null>(null)
  const [filters, setFilters] = useState<Filters>({ withImages: false, multipleImages: false, providers: new Set() })
  const [showFilters, setShowFilters] = useState(false)
  const [total, setTotal] = useState(0)
  const mapRef = useRef<L.Map | null>(null)
  const markersRef = useRef<Map<string, L.Marker>>(new Map())
  const layerGroupRef = useRef<L.LayerGroup | null>(null)
  const abortRef = useRef<AbortController | null>(null)

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

    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}?${params}`, { signal: abortRef.current.signal })
      const data = await res.json()
      setTotal(data.total ?? 0)
      setCafeMap(prev => {
        const next = new Map(prev)
        for (const c of (data.cafes ?? [])) {
          next.set(c.id, c)
        }
        return next
      })
    } catch (e) {
      if ((e as Error).name !== 'AbortError') console.error(e)
    } finally {
      setLoading(false)
    }
  }, [])

  const handleBoundsChange = useCallback((b: ViewportBounds) => {
    setViewport(b)
    fetchCafes(b, filters)
  }, [fetchCafes, filters])

  useEffect(() => {
    if (viewport) fetchCafes(viewport, filters)
  }, [filters]) // eslint-disable-line react-hooks/exhaustive-deps

  // Render markers
  useEffect(() => {
    if (!mapRef.current) return
    if (!layerGroupRef.current) {
      layerGroupRef.current = L.layerGroup().addTo(mapRef.current)
    }

    const layer = layerGroupRef.current
    const existing = markersRef.current
    const toRemove = new Set(existing.keys())

    for (const cafe of cafeMap.values()) {
      toRemove.delete(cafe.id)
      if (existing.has(cafe.id)) continue

      const providers = Array.isArray(cafe.providers) ? cafe.providers : JSON.parse(cafe.providers as unknown as string ?? '[]')
      const icon = makePieIcon(providers, 12, cafe.image_count > 0)
      const marker = L.marker([cafe.lat, cafe.lon], { icon })
      marker.on('click', () => setSelectedId(cafe.id))
      marker.addTo(layer)
      existing.set(cafe.id, marker)
    }

    for (const id of toRemove) {
      existing.get(id)?.remove()
      existing.delete(id)
    }
  }, [cafeMap])

  const cafesInView = useMemo(() => cafeMap.size, [cafeMap])

  return (
    <div className="relative w-screen h-screen">
      <MapContainer
        center={[37.5665, 126.978]}
        zoom={14}
        className="w-full h-full"
        ref={mapRef}
      >
        <TileLayer
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution='© <a href="https://openstreetmap.org">OSM</a>'
        />
        <ViewportTracker onBoundsChange={handleBoundsChange} />
      </MapContainer>

      {/* Top bar */}
      <div className="absolute top-2 left-1/2 -translate-x-1/2 z-[500] flex items-center gap-2">
        <div className="bg-white rounded-lg shadow px-3 py-1.5 text-sm font-medium flex items-center gap-2">
          <span className="text-gray-700">☕ Workcafe</span>
          <span className="text-gray-400">|</span>
          <span className="text-gray-500">
            {loading ? '…' : `${cafesInView} / ${total}`}
          </span>
        </div>
        <button
          className="bg-white rounded-lg shadow px-3 py-1.5 text-sm hover:bg-gray-50"
          onClick={() => setShowFilters(!showFilters)}
        >
          Filter {filters.withImages || filters.multipleImages || filters.providers.size > 0 ? '●' : ''}
        </button>
        <a
          href={SCRAPER_URL}
          className="bg-white rounded-lg shadow px-3 py-1.5 text-sm hover:bg-gray-50 text-gray-600"
        >
          Scraper →
        </a>
      </div>

      {/* Legend */}
      <div className="absolute bottom-6 left-2 z-[500] bg-white rounded-lg shadow p-2">
        <div className="text-xs text-gray-500 mb-1 font-medium">Providers</div>
        {Object.entries(PROVIDER_COLORS).map(([p, color]) => (
          <div key={p} className="flex items-center gap-1.5 text-xs py-0.5">
            <div className="w-3 h-3 rounded-full flex-shrink-0" style={{ background: color }} />
            <span>{p}</span>
          </div>
        ))}
        <div className="mt-1 pt-1 border-t text-xs text-gray-400">● = has images</div>
      </div>

      {/* Filter panel */}
      {showFilters && (
        <div className="absolute top-14 left-1/2 -translate-x-1/2 z-[500] bg-white rounded-lg shadow-lg p-4 w-72">
          <h3 className="font-medium mb-3">Filters</h3>
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
          <div className="text-xs text-gray-500 mb-1 font-medium">Providers</div>
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
          <button className="mt-2 text-xs text-gray-400 hover:text-gray-600"
            onClick={() => setFilters({ withImages: false, multipleImages: false, providers: new Set() })}>
            Clear all
          </button>
        </div>
      )}

      {/* Details pane */}
      {selectedId && (
        <CleanCafeDetailsPane
          cafeId={selectedId}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  )
}
