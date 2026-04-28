import { useEffect, useRef, useState, useMemo, useCallback } from 'react'
import { MapContainer, TileLayer, useMapEvents, useMap, ScaleControl } from 'react-leaflet'
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
import { SnapshotSelector, useSnapshot } from './components/SnapshotSelector'
import { TagBrowserOverlay } from './components/TagBrowserOverlay'
import { CustomWebsitesModal } from './components/CustomWebsitesModal'

interface ViewportBounds {
  minLat: number; maxLat: number; minLon: number; maxLon: number
}

interface Filters {
  withImages: boolean
  multipleImages: boolean
  providers: Set<string>
  chains: Set<string>
  tags: Set<string>
  customWebsite: boolean
}

interface TagCount {
  tag: string
  count: number
}

interface MarkerLayerProps {
  scraped_cafes: Map<string, CleanCafe>
  onSelect: (id: string) => void
}

const STARRED_TAGS_KEY = 'workcafe_starred_tags'

function useIsMobile() {
  const [isMobile, setIsMobile] = useState(() => typeof window !== 'undefined' && window.innerWidth < 768)
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)')
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches)
    mq.addEventListener('change', handler)
    setIsMobile(mq.matches)
    return () => mq.removeEventListener('change', handler)
  }, [])
  return isMobile
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
  useEffect(() => {
    const b = map.getBounds()
    onBoundsChange({ minLat: b.getSouth(), maxLat: b.getNorth(), minLon: b.getWest(), maxLon: b.getEast() })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return null
}

const MAP_POS_KEY = 'workcafe_map_pos'
const DEFAULT_CENTER: [number, number] = [37.4919824, 126.9907758]
const DEFAULT_ZOOM = 15

function loadMapPos(): { center: [number, number]; zoom: number } {
  try {
    const raw = localStorage.getItem(MAP_POS_KEY)
    if (raw) {
      const { lat, lng, zoom } = JSON.parse(raw)
      if (lat && lng && zoom) return { center: [lat, lng], zoom }
    }
  } catch {}
  return { center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM }
}

function MapPositionSaver() {
  useMapEvents({
    moveend: (e) => {
      const c = e.target.getCenter()
      const zoom = e.target.getZoom()
      localStorage.setItem(MAP_POS_KEY, JSON.stringify({ lat: c.lat, lng: c.lng, zoom }))
    },
  })
  return null
}

function MapPanner({ target }: { target: [number, number] | null }) {
  const map = useMap()
  useEffect(() => {
    if (target) map.setView(target, Math.max(map.getZoom(), 16), { animate: true })
  }, [target, map])
  return null
}

function LocationDotLayer({ location }: { location: [number, number] | null }) {
  const map = useMap()
  const markerRef = useRef<L.CircleMarker | null>(null)

  useEffect(() => {
    if (markerRef.current) { markerRef.current.remove(); markerRef.current = null }
    if (!location) return
    markerRef.current = L.circleMarker(location, {
      radius: 8,
      fillColor: '#3b82f6',
      color: '#ffffff',
      weight: 2.5,
      fillOpacity: 1,
    }).addTo(map)
  }, [location, map])

  useEffect(() => () => { markerRef.current?.remove() }, [])

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

const IS_PUBLIC = import.meta.env.VITE_IS_PUBLIC === 'true'

export default function CleanApp() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const { snapshot, setSnapshot, apiUrl } = useSnapshot()
  const [cafeMap, setCafeMap] = useState<Map<string, CleanCafe>>(new Map())
  const [loading, setLoading] = useState(true)
  const [filters, setFilters] = useState<Filters>({ withImages: false, multipleImages: false, providers: new Set(), chains: new Set(), tags: new Set(), customWebsite: false })
  const [showFilters, setShowFilters] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showTagBrowser, setShowTagBrowser] = useState(false)
  const [showCustomWebsites, setShowCustomWebsites] = useState(false)
  const [showMobileMenu, setShowMobileMenu] = useState(false)
  const [chains, setChains] = useState<Chain[]>([])
  const [availableTags, setAvailableTags] = useState<TagCount[]>([])
  const [total, setTotal] = useState(0)
  const [isLocating, setIsLocating] = useState(false)
  const [userLocation, setUserLocation] = useState<[number, number] | null>(null)
  const [tagSearch, setTagSearch] = useState('')
  const [starredTags, setStarredTags] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem(STARRED_TAGS_KEY)
      return raw ? new Set(JSON.parse(raw)) : new Set()
    } catch { return new Set() }
  })

  const isMobile = useIsMobile()
  const selectedId = id || null
  const [mapTarget, setMapTarget] = useState<[number, number] | null>(null)

  const toggleStarTag = useCallback((tag: string) => {
    setStarredTags(prev => {
      const next = new Set(prev)
      next.has(tag) ? next.delete(tag) : next.add(tag)
      localStorage.setItem(STARRED_TAGS_KEY, JSON.stringify([...next]))
      return next
    })
  }, [])

  useEffect(() => {
    fetch(apiUrl('/api/chains')).then(r => r.ok ? r.json() : []).then(data => setChains((data ?? []).slice(0, 30))).catch(console.error)
    fetch(apiUrl('/api/tags')).then(r => r.ok ? r.json() : []).then(data => setAvailableTags(data ?? [])).catch(console.error)
  }, [snapshot]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!selectedId) return
    fetch(apiUrl(`/api/clean_cafe?id=${encodeURIComponent(selectedId)}`))
      .then(r => r.json())
      .then(data => { if (data?.lat && data?.lon) setMapTarget([data.lat, data.lon]) })
      .catch(() => {})
  }, [selectedId, snapshot]) // eslint-disable-line react-hooks/exhaustive-deps

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
    if (f.tags.size > 0) params.set('tags', [...f.tags].join(','))
    if (f.customWebsite) params.set('customWebsite', 'true')

    setLoading(true)
    try {
      const res = await fetch(apiUrl(`/api/clean_cafes?${params}`), { signal: abortRef.current.signal })
      if (!res.ok) return
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

  useEffect(() => {
    if (boundsRef.current) {
      setCafeMap(new Map())
      fetchCafes(boundsRef.current, filters)
    }
  }, [filters, snapshot]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSelect = useCallback((cid: string) => navigate(`/cafe/${cid}`), [navigate])
  const cafesInView = useMemo(() => cafeMap.size, [cafeMap])

  const handleGPSClick = useCallback(() => {
    if ('geolocation' in navigator) {
      setIsLocating(true)
      navigator.geolocation.getCurrentPosition(
        (position) => {
          setIsLocating(false)
          const loc: [number, number] = [position.coords.latitude, position.coords.longitude]
          setUserLocation(loc)
          setMapTarget(loc)
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

  const clearFilters = useCallback(() => {
    setFilters({ withImages: false, multipleImages: false, providers: new Set(), chains: new Set(), tags: new Set(), customWebsite: false })
  }, [])

  const isFilterActive = filters.withImages || filters.multipleImages || filters.providers.size > 0 || filters.tags.size > 0 || filters.customWebsite || filters.chains.size > 0

  const displayTags = useMemo(() => {
    const search = tagSearch.toLowerCase()
    const filtered = search
      ? availableTags.filter(t => t.tag.toLowerCase().includes(search))
      : availableTags
    return [
      ...filtered.filter(t => starredTags.has(t.tag)),
      ...filtered.filter(t => !starredTags.has(t.tag)),
    ]
  }, [availableTags, starredTags, tagSearch])

  const displayChains = useMemo(() => {
    const search = tagSearch.toLowerCase()
    if (!search) return chains
    return chains.filter(c => (c.name_english || c.name).toLowerCase().includes(search))
  }, [chains, tagSearch])

  const { center: initialCenter, zoom: initialZoom } = loadMapPos()

  const filterContent = (
    <div className="flex flex-col gap-4">
      {/* Basic options — horizontal pill toggles */}
      <div>
        <div className="text-xs text-gray-500 mb-2 font-semibold uppercase tracking-wider">Options</div>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setFilters(f => ({ ...f, withImages: !f.withImages, multipleImages: f.withImages ? false : f.multipleImages }))}
            className={`px-3 py-1.5 rounded-full text-sm font-medium border transition-colors ${
              filters.withImages
                ? 'bg-blue-600 text-white border-blue-600'
                : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'
            }`}
          >
            Has images
          </button>
          <button
            onClick={() => setFilters(f => ({ ...f, multipleImages: !f.multipleImages, withImages: !f.multipleImages ? true : f.withImages }))}
            className={`px-3 py-1.5 rounded-full text-sm font-medium border transition-colors ${
              filters.multipleImages
                ? 'bg-blue-600 text-white border-blue-600'
                : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'
            }`}
          >
            Multiple images (2+)
          </button>
          <button
            onClick={() => setFilters(f => ({ ...f, customWebsite: !f.customWebsite }))}
            className={`px-3 py-1.5 rounded-full text-sm font-medium border transition-colors ${
              filters.customWebsite
                ? 'bg-blue-600 text-white border-blue-600'
                : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'
            }`}
          >
            Custom website 🌐
          </button>
        </div>
      </div>

      {/* Tags */}
      {availableTags.length > 0 && (
        <div>
          <div className="text-xs text-gray-500 mb-2 font-semibold uppercase tracking-wider">Tags</div>
          <input
            type="search"
            placeholder="Search tags & chains…"
            value={tagSearch}
            onChange={e => setTagSearch(e.target.value)}
            className="w-full mb-2 px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-blue-400"
          />
          <div className="flex flex-col gap-1">
            {starredTags.size > 0 && !tagSearch && (
              <div className="text-[10px] text-amber-500 font-semibold uppercase tracking-wider mb-0.5">★ Starred</div>
            )}
            {displayTags.map(({ tag, count }) => {
              const active = filters.tags.has(tag)
              const starred = starredTags.has(tag)
              return (
                <div key={tag} className="flex items-center gap-1">
                  <button
                    onClick={() => setFilters(f => {
                      const s = new Set(f.tags)
                      active ? s.delete(tag) : s.add(tag)
                      return { ...f, tags: s }
                    })}
                    className={`flex-1 flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg text-sm border transition-colors text-left ${
                      active
                        ? 'bg-blue-600 text-white border-blue-600'
                        : 'bg-white text-gray-600 border-gray-200 hover:border-blue-400'
                    }`}
                  >
                    <span>{tag}</span>
                    <span className={`text-xs font-medium shrink-0 ${active ? 'text-blue-100' : 'text-gray-400'}`}>{count}</span>
                  </button>
                  <button
                    onClick={() => toggleStarTag(tag)}
                    className={`px-1.5 py-1.5 rounded-lg text-sm transition-colors flex-shrink-0 ${starred ? 'text-amber-400 hover:text-amber-500' : 'text-gray-300 hover:text-amber-400'}`}
                    title={starred ? 'Unstar tag' : 'Star tag'}
                  >
                    {starred ? '★' : '☆'}
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Chains */}
      <div>
        <div className="text-xs text-gray-500 mb-2 font-semibold uppercase tracking-wider">Chains</div>
        <div className="space-y-1.5">
          {displayChains.map(c => {
            const chainColor = CHAIN_COLORS[c.name_english || c.name]
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
            )
          })}
        </div>
      </div>

      {/* Providers */}
      <div>
        <div className="text-xs text-gray-500 mb-2 font-semibold uppercase tracking-wider">Provider</div>
        <div className="space-y-1">
          {Object.entries(PROVIDER_COLORS).map(([p, color]) => (
            <div key={p} className="hover:bg-gray-50 p-1.5 -ml-1.5 rounded-lg transition-colors">
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
        </div>
      </div>
    </div>
  )

  return (
    <div className="relative w-screen h-screen">
      <MapContainer
        center={initialCenter}
        zoom={initialZoom}
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
        <MapPositionSaver />
        <MapPanner target={mapTarget} />
        <MarkerLayer scraped_cafes={cafeMap} onSelect={handleSelect} />
        <LocationDotLayer location={userLocation} />
        <ScaleControl position="bottomright" metric imperial={false} />
      </MapContainer>

      {/* Top left: Logo */}
      <div className="absolute top-2 left-2 z-[500] flex items-center gap-2 pointer-events-auto">
        <div className="bg-white rounded-lg shadow px-3 py-1.5 text-sm font-semibold text-gray-700 flex items-center gap-1.5">
          ☕ Workcafe
        </div>
        {/* Desktop: Custom Sites button */}
        <button
          className="hidden md:flex bg-white rounded-lg shadow px-3 py-1.5 text-sm hover:bg-gray-50 text-gray-500 items-center gap-1.5"
          onClick={() => setShowCustomWebsites(true)}
          title="Cafes with custom websites"
        >
          🌐 <span className="hidden lg:inline">Custom Sites</span>
        </button>
      </div>

      {/* Top right: Desktop buttons */}
      <div className="absolute top-2 right-2 z-[500] hidden md:flex items-center gap-2 pointer-events-auto">
        <button
          className={`bg-white rounded-lg shadow px-3 py-1.5 text-sm hover:bg-gray-50 transition-colors ${isFilterActive ? 'ring-2 ring-blue-400 text-blue-600' : 'text-gray-700'}`}
          onClick={() => setShowFilters(!showFilters)}
        >
          Filter {isFilterActive ? '●' : ''}
        </button>
        {!IS_PUBLIC && (
          <button
            className="bg-white rounded-lg shadow px-3 py-1.5 text-sm hover:bg-gray-50 text-gray-500"
            onClick={() => setShowSettings(true)}
          >
            Scraper Status
          </button>
        )}
        <button
          className="bg-white rounded-lg shadow px-3 py-1.5 text-sm hover:bg-gray-50 text-gray-500"
          onClick={() => setShowTagBrowser(true)}
        >
          Tags
        </button>
        <SnapshotSelector snapshot={snapshot} setSnapshot={setSnapshot} />
      </div>

      {/* Top right: Mobile Filter + Hamburger */}
      <div className="absolute top-2 right-2 z-[500] flex md:hidden items-center gap-2 pointer-events-auto">
        <button
          onClick={() => setShowFilters(true)}
          className={`bg-white rounded-full shadow w-9 h-9 flex items-center justify-center transition-colors ${isFilterActive ? 'ring-2 ring-blue-400 text-blue-600' : 'text-gray-600 hover:bg-gray-50'}`}
          title="Filters"
        >
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-5 h-5">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 0 1-.659 1.591L15.25 12.5v6.25a.75.75 0 0 1-.75.75h-5a.75.75 0 0 1-.75-.75V12.5L3.659 7.409A2.25 2.25 0 0 1 3 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0 1 12 3Z" />
          </svg>
        </button>
        <button
          onClick={() => setShowMobileMenu(true)}
          className="bg-white rounded-full shadow w-9 h-9 flex items-center justify-center text-xl text-gray-700 hover:bg-gray-50"
          title="Menu"
        >
          ☰
        </button>
      </div>

      {/* Bottom right: Counts + GPS */}
      <div className="absolute bottom-10 right-2 z-[500] flex flex-col items-end gap-2 pointer-events-auto">
        <button
          onClick={handleGPSClick}
          disabled={isLocating}
          className={`bg-white rounded-full shadow-lg w-11 h-11 flex items-center justify-center transition-colors ${isLocating ? 'text-blue-500 bg-blue-50' : 'text-gray-700 hover:bg-gray-50'}`}
          title="Go to my location"
        >
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className={`w-5 h-5 ${isLocating ? 'animate-pulse' : ''}`}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 2.25v2.25m0 15v2.25M2.25 12h2.25m15 0h2.25M5.25 12a6.75 6.75 0 1 1 13.5 0 6.75 6.75 0 0 1-13.5 0Z" />
          </svg>
        </button>
      </div>

      {/* Bottom right: Cafe counts badge */}
      <div className="absolute bottom-2 right-2 z-[500] pointer-events-none">
        <div className="bg-white/90 rounded-lg shadow px-2 py-0.5 text-[11px] text-gray-500 font-medium">
          {loading ? '…' : `${cafesInView.toLocaleString()} / ${total.toLocaleString()}`}
        </div>
      </div>

      {/* Provider legend - desktop only */}
      <div className="absolute bottom-10 left-2 z-[500] hidden md:block bg-white/90 rounded-lg shadow p-2 text-xs">
        <div className="text-gray-500 mb-1 font-medium">Providers</div>
        {Object.entries(PROVIDER_COLORS).map(([p, color]) => (
          <div key={p} className="flex items-center gap-1.5 py-0.5">
            <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: color }} />
            <span className="text-gray-600">{p}</span>
          </div>
        ))}
        <div className="mt-1 pt-1 border-t text-gray-400">black ring = has images</div>
      </div>

      {/* Desktop filter panel */}
      {showFilters && !isMobile && (
        <div className="absolute top-14 right-3 z-[600] bg-white rounded-xl shadow-2xl overflow-y-auto" style={{ maxHeight: 'calc(100vh - 80px)', minWidth: 480 }}>
          <div className="p-6 flex gap-8">
            {/* Column 1 */}
            <div className="w-44 shrink-0">
              <div className="flex items-center justify-between mb-4">
                <h3 className="font-semibold text-base">Filters</h3>
                <button className="text-gray-400 hover:text-gray-600 text-xl" onClick={() => setShowFilters(false)}>✕</button>
              </div>
              {/* Options pill toggles */}
              <div className="text-xs text-gray-500 mb-2 font-semibold uppercase tracking-wider">Options</div>
              <div className="flex flex-col gap-1.5 mb-4">
                <button
                  onClick={() => setFilters(f => ({ ...f, withImages: !f.withImages, multipleImages: f.withImages ? false : f.multipleImages }))}
                  className={`px-3 py-1.5 rounded-full text-sm font-medium border transition-colors text-left ${
                    filters.withImages
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'
                  }`}
                >
                  Has images
                </button>
                <button
                  onClick={() => setFilters(f => ({ ...f, multipleImages: !f.multipleImages, withImages: !f.multipleImages ? true : f.withImages }))}
                  className={`px-3 py-1.5 rounded-full text-sm font-medium border transition-colors text-left ${
                    filters.multipleImages
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'
                  }`}
                >
                  Multiple images (2+)
                </button>
                <button
                  onClick={() => setFilters(f => ({ ...f, customWebsite: !f.customWebsite }))}
                  className={`px-3 py-1.5 rounded-full text-sm font-medium border transition-colors text-left ${
                    filters.customWebsite
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'
                  }`}
                >
                  Custom website 🌐
                </button>
              </div>
              {/* Chains */}
              <div className="text-xs text-gray-500 mb-3 font-semibold uppercase tracking-wider mt-4">Chains</div>
              <div className="space-y-1.5">
                {displayChains.map(c => {
                  const chainColor = CHAIN_COLORS[c.name_english || c.name]
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
                  )
                })}
              </div>
              <button className="mt-6 text-sm font-medium text-gray-400 hover:text-gray-800 transition-colors"
                onClick={clearFilters}>
                Clear all
              </button>
            </div>

            {/* Column 2: Tags */}
            {availableTags.length > 0 && (
              <div className="w-52 shrink-0">
                <div className="font-semibold mb-3 text-base">Tags</div>
                <input
                  type="search"
                  placeholder="Search tags & chains…"
                  value={tagSearch}
                  onChange={e => setTagSearch(e.target.value)}
                  className="w-full mb-2 px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-blue-400"
                />
                {starredTags.size > 0 && !tagSearch && (
                  <div className="text-[10px] text-amber-500 font-semibold uppercase tracking-wider mb-1">★ Starred</div>
                )}
                <div className="flex flex-col gap-1">
                  {displayTags.map(({ tag, count }) => {
                    const active = filters.tags.has(tag)
                    const starred = starredTags.has(tag)
                    return (
                      <div key={tag} className="flex items-center gap-1">
                        <button
                          onClick={() => setFilters(f => {
                            const s = new Set(f.tags)
                            active ? s.delete(tag) : s.add(tag)
                            return { ...f, tags: s }
                          })}
                          className={`flex-1 flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg text-sm border transition-colors text-left ${
                            active
                              ? 'bg-blue-600 text-white border-blue-600'
                              : 'bg-white text-gray-600 border-gray-200 hover:border-blue-400'
                          }`}
                        >
                          <span>{tag}</span>
                          <span className={`text-xs font-medium shrink-0 ${active ? 'text-blue-100' : 'text-gray-400'}`}>{count}</span>
                        </button>
                        <button
                          onClick={() => toggleStarTag(tag)}
                          className={`px-1 py-1.5 rounded-lg text-sm transition-colors flex-shrink-0 ${starred ? 'text-amber-400 hover:text-amber-500' : 'text-[#444] hover:text-amber-400'}`}
                          title={starred ? 'Unstar' : 'Star'}
                        >
                          {starred ? '★' : '☆'}
                        </button>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {/* Column 3: Provider */}
            <div className="w-44 shrink-0">
              <div className="font-semibold mb-4 text-base">Provider</div>
              <div className="space-y-1.5">
                {Object.entries(PROVIDER_COLORS).map(([p, color]) => (
                  <div key={p} className="hover:bg-gray-50 p-1.5 -ml-1.5 rounded-lg transition-colors">
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
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Mobile filter panel: full screen */}
      {showFilters && isMobile && (
        <div className="fixed inset-0 z-[1000] bg-white flex flex-col">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 bg-white">
            <h2 className="font-semibold text-base">Filters</h2>
            <div className="flex items-center gap-3">
              {isFilterActive && (
                <button className="text-sm text-gray-400 hover:text-gray-700" onClick={clearFilters}>Clear all</button>
              )}
              <button onClick={() => setShowFilters(false)} className="text-gray-400 hover:text-gray-600 text-xl w-8 h-8 flex items-center justify-center">✕</button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-4">
            {filterContent}
          </div>
          <div className="p-4 border-t bg-white">
            <button
              onClick={() => setShowFilters(false)}
              className="w-full py-3 rounded-xl bg-blue-600 text-white font-medium text-sm hover:bg-blue-700 transition-colors"
            >
              Apply{isFilterActive ? ` (${[...filters.tags, ...filters.providers, ...filters.chains].length + (filters.withImages ? 1 : 0) + (filters.multipleImages ? 1 : 0) + (filters.customWebsite ? 1 : 0)} active)` : ''}
            </button>
          </div>
        </div>
      )}

      {/* Mobile hamburger panel */}
      {showMobileMenu && (
        <div className="fixed inset-0 z-[1000] flex">
          <div className="flex-1 bg-black/30" onClick={() => setShowMobileMenu(false)} />
          <div className="bg-white w-72 h-full flex flex-col shadow-xl">
            <div className="flex items-center justify-between px-4 py-4 border-b border-gray-100">
              <span className="font-semibold text-gray-800">☕ Workcafe</span>
              <button onClick={() => setShowMobileMenu(false)} className="text-gray-400 hover:text-gray-600 text-xl w-8 h-8 flex items-center justify-center">✕</button>
            </div>
            <div className="flex-1 overflow-y-auto">
              <button
                onClick={() => { setShowMobileMenu(false); setShowTagBrowser(true) }}
                className="w-full flex items-center gap-4 px-5 py-4 hover:bg-gray-50 border-b border-gray-100 text-left"
              >
                <span className="text-xl w-7">🏷️</span>
                <span className="text-sm font-medium text-gray-700">Tag Browser</span>
              </button>
              {!IS_PUBLIC && (
                <button
                  onClick={() => { setShowMobileMenu(false); setShowSettings(true) }}
                  className="w-full flex items-center gap-4 px-5 py-4 hover:bg-gray-50 border-b border-gray-100 text-left"
                >
                  <span className="text-xl w-7">📊</span>
                  <span className="text-sm font-medium text-gray-700">Scraper Status</span>
                </button>
              )}
              <button
                onClick={() => { setShowMobileMenu(false); setShowCustomWebsites(true) }}
                className="w-full flex items-center gap-4 px-5 py-4 hover:bg-gray-50 border-b border-gray-100 text-left"
              >
                <span className="text-xl w-7">🌐</span>
                <span className="text-sm font-medium text-gray-700">Custom Websites</span>
              </button>
              <div className="px-5 py-4 border-b border-gray-100">
                <div className="flex items-center gap-4 mb-3">
                  <span className="text-xl w-7">🕐</span>
                  <span className="text-sm font-medium text-gray-700">DB Snapshot</span>
                </div>
                <SnapshotSelector snapshot={snapshot} setSnapshot={setSnapshot} />
              </div>
            </div>
            {/* Counts at bottom of menu */}
            <div className="px-5 py-3 border-t border-gray-100">
              <div className="text-xs text-gray-400">
                {loading ? 'Loading…' : `${cafesInView.toLocaleString()} shown / ${total.toLocaleString()} total`}
              </div>
            </div>
          </div>
        </div>
      )}

      {selectedId && (
        <CleanCafeDetailsPane
          cafeId={selectedId}
          onClose={() => navigate('/')}
          activeTags={filters.tags}
          starredTags={starredTags}
          isMobile={isMobile}
        />
      )}

      {selectedId && location.search && (
        <CafeDetailsPage activeTags={filters.tags} />
      )}

      {!IS_PUBLIC && showSettings && (
        <SettingsModal onClose={() => setShowSettings(false)} />
      )}

      {showTagBrowser && (
        <TagBrowserOverlay onClose={() => setShowTagBrowser(false)} />
      )}

      {showCustomWebsites && (
        <CustomWebsitesModal
          onClose={() => setShowCustomWebsites(false)}
          onSelectCafe={id => navigate(`/cafe/${id}`)}
        />
      )}
    </div>
  )
}
