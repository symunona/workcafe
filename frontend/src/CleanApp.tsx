import { useEffect, useRef, useState, useMemo, useCallback, type ReactNode } from 'react'
import { MapContainer, TileLayer, useMapEvents, useMap, ScaleControl } from 'react-leaflet'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { CleanCafe, Chain } from './types'
import { PROVIDER_COLORS } from './utils'
import { makePieIcon, makeStarIcon, CHAIN_COLORS, CAFE_BROWN_HIGHLIGHT } from './utils_clean'
import { CleanCafeDetailsPane } from './components/CleanCafeDetailsPane'
import { CafeDetailsPage } from './components/CafeDetailsPage'
import { SettingsModal } from './components/SettingsModal'
import { Checkbox } from './components/Checkbox'
import { SnapshotSelector, useSnapshot } from './components/SnapshotSelector'
import { TagBrowserOverlay } from './components/TagBrowserOverlay'
import { AboutModal } from './components/AboutModal'
import { SeoulSubwayLayer } from './components/SeoulSubwayLayer'
import { ScrapeCoverageLayer, type CoverageResponse } from './components/ScrapeCoverageLayer'

declare const __GIT_SHA__: string
declare const __BUILD_DATE__: string

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
  showSourceColors: boolean
  showBrandColors: boolean
  showImageRing: boolean
  favoriteIds: Set<string>
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

function parseHashPos(): { center: [number, number]; zoom: number } | null {
  try {
    const parts = window.location.hash.slice(1).split('/')
    if (parts.length >= 3) {
      const zoom = parseInt(parts[0])
      const lat = parseFloat(parts[1])
      const lon = parseFloat(parts[2])
      if (!isNaN(zoom) && !isNaN(lat) && !isNaN(lon) &&
          lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180 &&
          zoom >= 1 && zoom <= 20)
        return { center: [lat, lon], zoom }
    }
  } catch {}
  return null
}

function loadMapPos(): { center: [number, number]; zoom: number } {
  const fromHash = parseHashPos()
  if (fromHash) return fromHash
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
      const hash = `#${zoom}/${c.lat.toFixed(5)}/${c.lng.toFixed(5)}`
      history.replaceState(null, '', window.location.pathname + window.location.search + hash)
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

  // Dedicated pane above markerPane (600) so the blue dot never hides behind cafe markers.
  useEffect(() => {
    if (!map.getPane('userDot')) {
      const pane = map.createPane('userDot')
      pane.style.zIndex = '650'
    }
  }, [map])

  useEffect(() => {
    if (!location) {
      if (markerRef.current) { markerRef.current.remove(); markerRef.current = null }
      return
    }
    // Move existing dot in place (smooth on walk updates) instead of remove+recreate.
    if (markerRef.current) {
      markerRef.current.setLatLng(location)
      return
    }
    markerRef.current = L.circleMarker(location, {
      pane: 'userDot',
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

function MarkerLayer({ scraped_cafes, onSelect, showSourceColors, showBrandColors, showImageRing, favoriteIds }: MarkerLayerProps) {
  const map = useMap()
  const markersRef = useRef<Map<string, L.Marker>>(new Map())
  const layerRef = useRef<L.LayerGroup | null>(null)
  const colorKeyRef = useRef(`${showSourceColors}-${showBrandColors}-${showImageRing}-`)

  useEffect(() => {
    if (!layerRef.current) {
      layerRef.current = L.layerGroup().addTo(map)
    }
    return () => {
      layerRef.current?.remove()
      layerRef.current = null
      markersRef.current.clear()
    }
  }, [map])

  useEffect(() => {
    const layer = layerRef.current
    if (!layer) return

    const favKey = [...favoriteIds].sort().join(',')
    const newColorKey = `${showSourceColors}-${showBrandColors}-${showImageRing}-${favKey}`
    const colorChanged = newColorKey !== colorKeyRef.current
    colorKeyRef.current = newColorKey

    const existing = markersRef.current
    const toRemove = new Set(existing.keys())

    for (const cafe of scraped_cafes.values()) {
      toRemove.delete(cafe.id)
      if (existing.has(cafe.id) && !colorChanged) continue

      existing.get(cafe.id)?.remove()

      const isFav = favoriteIds.has(cafe.id)
      const providers = Array.isArray(cafe.providers)
        ? cafe.providers
        : (JSON.parse(cafe.providers as unknown as string ?? '[]') as string[])
      const chainName = cafe.chain_name_english || cafe.chain_name
      const icon = isFav
        ? makeStarIcon(18, (chainName && CHAIN_COLORS[chainName]) || CAFE_BROWN_HIGHLIGHT)
        : makePieIcon(providers, 14, cafe.image_count > 0, chainName, showSourceColors, showBrandColors, showImageRing)
      const marker = L.marker([cafe.lat, cafe.lon], { icon })
      marker.on('click', () => onSelect(cafe.id))
      marker.addTo(layer)
      existing.set(cafe.id, marker)
    }

    for (const id of toRemove) {
      existing.get(id)?.remove()
      existing.delete(id)
    }
  }, [scraped_cafes, onSelect, showSourceColors, showBrandColors, showImageRing, favoriteIds])

  return null
}

const HEATMAP_PALETTE = (() => {
  const p = new Uint8ClampedArray(256 * 4)
  const stops = [
    [0,   0,   0,   0  ],
    [0,   0,   255, 120],
    [0,   200, 255, 160],
    [0,   255, 100, 190],
    [255, 255, 0,   210],
    [255, 120, 0,   230],
    [255, 0,   0,   255],
  ]
  for (let i = 0; i < 256; i++) {
    const t = i / 255
    const si = t * (stops.length - 1)
    const lo = Math.floor(si)
    const hi = Math.min(lo + 1, stops.length - 1)
    const f = si - lo
    for (let c = 0; c < 4; c++) {
      p[i * 4 + c] = Math.round(stops[lo][c] + f * (stops[hi][c] - stops[lo][c]))
    }
  }
  return p
})()

function HeatmapLayer({ points, radiusMult }: { points: [number, number][]; radiusMult: number }) {
  const map = useMap()
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = document.createElement('canvas')
    canvas.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;z-index:400;'
    map.getContainer().appendChild(canvas)
    canvasRef.current = canvas
    return () => { canvas.remove(); canvasRef.current = null }
  }, [map])

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const size = map.getSize()
    canvas.width = size.x
    canvas.height = size.y
    const ctx = canvas.getContext('2d')!
    ctx.clearRect(0, 0, canvas.width, canvas.height)

    const zoom = map.getZoom()
    const r = Math.max(3, Math.min(80, radiusMult * Math.pow(2, (zoom - 15) * 0.7)))

    // Pass 1: white intensity blobs on offscreen canvas
    const off = document.createElement('canvas')
    off.width = canvas.width
    off.height = canvas.height
    const offCtx = off.getContext('2d')!
    for (const [lat, lon] of points) {
      const pt = map.latLngToContainerPoint([lat, lon])
      if (pt.x < -r || pt.x > canvas.width + r || pt.y < -r || pt.y > canvas.height + r) continue
      const grad = offCtx.createRadialGradient(pt.x, pt.y, 0, pt.x, pt.y, r)
      grad.addColorStop(0, 'rgba(255,255,255,0.08)')
      grad.addColorStop(1, 'rgba(255,255,255,0)')
      offCtx.fillStyle = grad
      offCtx.beginPath()
      offCtx.arc(pt.x, pt.y, r, 0, Math.PI * 2)
      offCtx.fill()
    }

    // Pass 2: colorize using palette
    const img = offCtx.getImageData(0, 0, off.width, off.height)
    const d = img.data
    for (let i = 0; i < d.length; i += 4) {
      const v = d[i + 3] // alpha = accumulated intensity
      d[i]   = HEATMAP_PALETTE[v * 4]
      d[i+1] = HEATMAP_PALETTE[v * 4 + 1]
      d[i+2] = HEATMAP_PALETTE[v * 4 + 2]
      d[i+3] = HEATMAP_PALETTE[v * 4 + 3]
    }
    ctx.putImageData(img, 0, 0)
  }, [map, points, radiusMult])

  useEffect(() => {
    draw()
    map.on('move zoom moveend zoomend', draw)
    return () => { map.off('move zoom moveend zoomend', draw) }
  }, [map, draw])

  return null
}

interface VisitEntry {
  id: string
  name: string
  lat: number
  lon: number
  timestamp: number
}

const RECENTLY_KEY = 'workcafe_recently_visited'
const FAVORITES_KEY = 'workcafe_favorites'

interface FavoriteEntry {
  id: string
  name: string
  lat: number
  lon: number
  timestamp: number
}

function useFavorites() {
  const [favorites, setFavorites] = useState<FavoriteEntry[]>(() => {
    try {
      const raw = localStorage.getItem(FAVORITES_KEY)
      return raw ? JSON.parse(raw) : []
    } catch { return [] }
  })
  const favoriteIds = useMemo(() => new Set(favorites.map(f => f.id)), [favorites])
  const toggleFavorite = useCallback((entry: Omit<FavoriteEntry, 'timestamp'>) => {
    setFavorites(prev => {
      const exists = prev.some(f => f.id === entry.id)
      const next = exists
        ? prev.filter(f => f.id !== entry.id)
        : [{ ...entry, timestamp: Date.now() }, ...prev]
      localStorage.setItem(FAVORITES_KEY, JSON.stringify(next))
      return next
    })
  }, [])
  const clearFavorites = useCallback(() => {
    localStorage.removeItem(FAVORITES_KEY)
    setFavorites([])
  }, [])
  return { favorites, favoriteIds, toggleFavorite, clearFavorites }
}

function timeAgo(ts: number): string {
  const diff = Date.now() - ts
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function useRecentlyVisited() {
  const [visits, setVisits] = useState<VisitEntry[]>(() => {
    try {
      const raw = localStorage.getItem(RECENTLY_KEY)
      return raw ? JSON.parse(raw) : []
    } catch { return [] }
  })
  const addVisit = useCallback((entry: VisitEntry) => {
    setVisits(prev => {
      const next = [entry, ...prev.filter(v => v.id !== entry.id)].slice(0, 100)
      localStorage.setItem(RECENTLY_KEY, JSON.stringify(next))
      return next
    })
  }, [])
  const removeVisit = useCallback((id: string) => {
    setVisits(prev => {
      const next = prev.filter(v => v.id !== id)
      localStorage.setItem(RECENTLY_KEY, JSON.stringify(next))
      return next
    })
  }, [])
  const clearVisits = useCallback(() => {
    localStorage.removeItem(RECENTLY_KEY)
    setVisits([])
  }, [])
  return { visits, addVisit, removeVisit, clearVisits }
}

function RecentlyVisitedLayer({ visits, selectedId, visibleIds }: { visits: VisitEntry[]; selectedId: string | null; visibleIds: Set<string> }) {
  const map = useMap()
  const layerRef = useRef<L.LayerGroup | null>(null)

  useEffect(() => {
    if (!layerRef.current) layerRef.current = L.layerGroup().addTo(map)
    const layer = layerRef.current
    layer.clearLayers()
    for (const v of visits.filter(v => visibleIds.has(v.id))) {
      const isSel = v.id === selectedId
      L.circleMarker([v.lat, v.lon], {
        radius: isSel ? 14 : 9,
        color: isSel ? '#ef4444' : '#f59e0b',
        fillColor: 'transparent',
        fillOpacity: 0,
        weight: isSel ? 3 : 2,
        interactive: false,
      }).addTo(layer)
    }
  }, [map, visits, selectedId, visibleIds])

  useEffect(() => () => { layerRef.current?.remove(); layerRef.current = null }, [map])

  return null
}

function ScalePositioner() {
  const map = useMap()
  useEffect(() => {
    const el = map.getContainer().querySelector('.leaflet-control-scale') as HTMLElement | null
    if (el) el.style.marginRight = '68px'
  }, [map])
  return null
}

const IS_PUBLIC = import.meta.env.VITE_IS_PUBLIC === 'true'
const IS_DEVMODE = import.meta.env.VITE_IS_DEVMODE === 'true'

function highlight(text: string, q: string): ReactNode {
  if (!q) return text
  const idx = text.toLowerCase().indexOf(q.toLowerCase())
  if (idx < 0) return text
  return (
    <>{text.slice(0, idx)}<mark className="bg-yellow-200 rounded-sm not-italic px-0.5">{text.slice(idx, idx + q.length)}</mark>{text.slice(idx + q.length)}</>
  )
}

interface SearchBarProps {
  query: string
  setQuery: (q: string) => void
  results: CleanCafe[]
  totalCount: number
  onSelect: (cafe: CleanCafe) => void
  className?: string
}

function SearchBar({ query, setQuery, results, totalCount, onSelect, className = '' }: SearchBarProps) {
  const [focused, setFocused] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const show = focused && query.trim().length > 0

  useEffect(() => {
    if (!show) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setQuery(''); inputRef.current?.blur() }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [show, setQuery])

  return (
    <div className={`relative ${className}`}>
      <div className="relative">
        <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400 pointer-events-none" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
        </svg>
        <input
          ref={inputRef}
          type="search"
          placeholder="Search cafes…"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setTimeout(() => setFocused(false), 150)}
          className="w-full pl-8 pr-7 py-1.5 text-sm bg-white rounded-lg shadow border border-transparent focus:outline-none focus:border-blue-300 focus:ring-1 focus:ring-blue-100"
        />
        {query && (
          <button onClick={() => setQuery('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-300 hover:text-gray-500 text-xl leading-none">×</button>
        )}
      </div>
      {show && (
        <div className="absolute top-full left-0 right-0 mt-1 bg-white rounded-xl shadow-2xl z-[700] overflow-hidden border border-gray-100">
          <div className="px-3 py-2 text-xs bg-gray-50 border-b border-gray-100 flex items-center justify-between">
            <span className="font-semibold text-gray-500">
              {totalCount === 0 ? 'No results' : `${totalCount}${totalCount >= 50 ? '+' : ''} cafe${totalCount !== 1 ? 's' : ''}`}
            </span>
            {totalCount > 0 && <span className="text-gray-300">↵ to navigate</span>}
          </div>
          <div className="max-h-72 overflow-y-auto divide-y divide-gray-50">
            {results.map(cafe => (
              <button
                key={cafe.id}
                onMouseDown={() => { onSelect(cafe); setFocused(false) }}
                className="w-full text-left px-3 py-2.5 hover:bg-blue-50 transition-colors"
              >
                <div className="font-medium text-sm text-gray-900 truncate">{highlight(cafe.name, query)}</div>
                {cafe.english_name && cafe.english_name !== cafe.name && (
                  <div className="text-xs text-gray-500 truncate">{highlight(cafe.english_name, query)}</div>
                )}
                {cafe.address && (
                  <div className="text-xs text-gray-400 truncate mt-0.5">{highlight(cafe.address, query)}</div>
                )}
              </button>
            ))}
            {totalCount > results.length && (
              <div className="px-3 py-2 text-xs text-gray-400 italic bg-gray-50">
                +{totalCount - results.length} more — refine search
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

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
  const [showMobileMenu, setShowMobileMenu] = useState(false)
  const [showStats, setShowStats] = useState(false)
  const [chains, setChains] = useState<Chain[]>([])
  const [availableTags, setAvailableTags] = useState<TagCount[]>([])
  const [total, setTotal] = useState(0)
  const [isLocating, setIsLocating] = useState(false)
  const [userLocation, setUserLocation] = useState<[number, number] | null>(null)
  const watchIdRef = useRef<number | null>(null)
  const [tagSearch, setTagSearch] = useState('')
  const [starredTags, setStarredTags] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem(STARRED_TAGS_KEY)
      return raw ? new Set(JSON.parse(raw)) : new Set()
    } catch { return new Set() }
  })

  const [showAbout, setShowAbout] = useState(false)
  const [showMapSettings, setShowMapSettings] = useState(false)
  const [showLayers, setShowLayers] = useState(false)
  const [showTransitLayer, setShowTransitLayer] = useState(() => {
    try { return JSON.parse(localStorage.getItem('wc_transit_layer') ?? 'false') } catch { return false }
  })
  const [showSubwayLayer, setShowSubwayLayer] = useState(() => {
    try { return JSON.parse(localStorage.getItem('wc_subway_layer') ?? 'false') } catch { return false }
  })
  const [subwayLoading, setSubwayLoading] = useState(false)
  // Scrape-coverage overlay (admin-only): rectangles per 1km cell, heat by cafe count.
  const [showScrapeCoverage, setShowScrapeCoverage] = useState(() => {
    try { return !IS_PUBLIC && JSON.parse(localStorage.getItem('wc_scrape_coverage') ?? 'false') } catch { return false }
  })
  const [coverageRollup, setCoverageRollup] = useState<CoverageResponse | null>(null)
  const [showHeatmap, setShowHeatmap] = useState(() => {
    try { return JSON.parse(localStorage.getItem('wc_heatmap') ?? 'false') } catch { return false }
  })
  const [heatmapRadius, setHeatmapRadius] = useState(() => {
    try { return JSON.parse(localStorage.getItem('wc_heatmap_r') ?? '200') } catch { return 200 }
  })
  const [showSourceColors, setShowSourceColors] = useState(() => {
    try { return JSON.parse(localStorage.getItem('wc_source_colors') ?? 'false') } catch { return false }
  })
  const [showBrandColors, setShowBrandColors] = useState(() => {
    try { return JSON.parse(localStorage.getItem('wc_brand_colors') ?? 'false') } catch { return false }
  })
  const [showImageRing, setShowImageRing] = useState(() => {
    try { return JSON.parse(localStorage.getItem('wc_image_ring') ?? 'false') } catch { return false }
  })
  const [showRecently, setShowRecently] = useState(false)
  const [onlyMostRecent, setOnlyMostRecent] = useState(false)
  const { visits, addVisit, removeVisit, clearVisits } = useRecentlyVisited()
  const { favorites, favoriteIds, toggleFavorite, clearFavorites } = useFavorites()
  const [showFavorites, setShowFavorites] = useState(false)
  const [onlyFavorites, setOnlyFavorites] = useState(false)
  const [heatmapPoints, setHeatmapPoints] = useState<[number, number][]>([])
  const heatmapAbortRef = useRef<AbortController | null>(null)

  useEffect(() => { localStorage.setItem('wc_heatmap', JSON.stringify(showHeatmap)) }, [showHeatmap])
  useEffect(() => { localStorage.setItem('wc_heatmap_r', JSON.stringify(heatmapRadius)) }, [heatmapRadius])
  useEffect(() => { localStorage.setItem('wc_source_colors', JSON.stringify(showSourceColors)) }, [showSourceColors])
  useEffect(() => { localStorage.setItem('wc_brand_colors', JSON.stringify(showBrandColors)) }, [showBrandColors])
  useEffect(() => { localStorage.setItem('wc_image_ring', JSON.stringify(showImageRing)) }, [showImageRing])
  useEffect(() => { localStorage.setItem('wc_transit_layer', JSON.stringify(showTransitLayer)) }, [showTransitLayer])
  useEffect(() => { localStorage.setItem('wc_subway_layer', JSON.stringify(showSubwayLayer)) }, [showSubwayLayer])
  useEffect(() => { localStorage.setItem('wc_scrape_coverage', JSON.stringify(showScrapeCoverage)) }, [showScrapeCoverage])

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
      .then(data => {
        const lat = data?.avg_lat ?? data?.lat
        const lon = data?.avg_lon ?? data?.lon
        if (lat && lon) setMapTarget([lat, lon])
      })
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

  const fetchHeatmap = useCallback(async (bounds: ViewportBounds, f: Filters) => {
    if (heatmapAbortRef.current) heatmapAbortRef.current.abort()
    heatmapAbortRef.current = new AbortController()

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
      const res = await fetch(apiUrl(`/api/heatmap?${params}`), { signal: heatmapAbortRef.current.signal })
      if (!res.ok) return
      const data = await res.json()
      setTotal(data.total ?? 0)
      setHeatmapPoints(data.points ?? [])
    } catch (e) {
      if ((e as Error).name !== 'AbortError') console.error(e)
    } finally {
      setLoading(false)
    }
  }, [apiUrl])

  const handleBoundsChange = useCallback((b: ViewportBounds) => {
    boundsRef.current = b
    if (showHeatmap) fetchHeatmap(b, filters)
    else fetchCafes(b, filters)
  }, [fetchCafes, fetchHeatmap, filters, showHeatmap])

  useEffect(() => {
    if (boundsRef.current) {
      if (showHeatmap) {
        setHeatmapPoints([])
        fetchHeatmap(boundsRef.current, filters)
      } else {
        setCafeMap(new Map())
        fetchCafes(boundsRef.current, filters)
      }
    }
  }, [filters, snapshot, showHeatmap]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSelect = useCallback((cid: string) => {
    const cafe = cafeMap.get(cid)
    if (cafe) addVisit({ id: cid, name: cafe.name, lat: cafe.lat, lon: cafe.lon, timestamp: Date.now() })
    navigate(`/cafe/${cid}`)
  }, [navigate, cafeMap, addVisit])

  const handleGPSClick = useCallback(() => {
    if (!('geolocation' in navigator)) {
      alert('Geolocation is not supported by your browser')
      return
    }
    setIsLocating(true)
    let centered = false
    // Already watching → clear before starting fresh.
    if (watchIdRef.current !== null) {
      navigator.geolocation.clearWatch(watchIdRef.current)
      watchIdRef.current = null
    }
    watchIdRef.current = navigator.geolocation.watchPosition(
      (position) => {
        setIsLocating(false)
        const loc: [number, number] = [position.coords.latitude, position.coords.longitude]
        setUserLocation(loc)
        // Recenter only on first fix; later updates just move the dot as user walks.
        if (!centered) { setMapTarget(loc); centered = true }
      },
      (error) => {
        setIsLocating(false)
        console.error('Error getting location:', error)
        alert('Unable to retrieve your location')
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
    )
  }, [])

  // Stop watching geolocation on unmount.
  useEffect(() => () => {
    if (watchIdRef.current !== null) navigator.geolocation.clearWatch(watchIdRef.current)
  }, [])

  const [searchQuery, setSearchQuery] = useState('')

  const filteredCafeMap = useMemo(() => {
    let map = cafeMap
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      const result = new Map<string, CleanCafe>()
      for (const [cid, cafe] of cafeMap) {
        if (cafe.name.toLowerCase().includes(q) ||
            cafe.english_name?.toLowerCase().includes(q) ||
            cafe.address?.toLowerCase().includes(q)) {
          result.set(cid, cafe)
        }
      }
      map = result
    }
    if (onlyFavorites) {
      const result = new Map<string, CleanCafe>()
      for (const [cid, cafe] of map) {
        if (favoriteIds.has(cid)) result.set(cid, cafe)
      }
      return result
    }
    return map
  }, [cafeMap, searchQuery, onlyFavorites, favoriteIds])

  const searchResults = useMemo(() => [...filteredCafeMap.values()].slice(0, 8), [filteredCafeMap])
  const filteredCafeIds = useMemo(() => new Set(filteredCafeMap.keys()), [filteredCafeMap])

  const handleSearchSelect = useCallback((cafe: CleanCafe) => {
    addVisit({ id: cafe.id, name: cafe.name, lat: cafe.lat, lon: cafe.lon, timestamp: Date.now() })
    setMapTarget([cafe.lat, cafe.lon])
    navigate(`/cafe/${cafe.id}`)
    setSearchQuery('')
  }, [navigate, addVisit])

  const clearFilters = useCallback(() => {
    setFilters({ withImages: false, multipleImages: false, providers: new Set(), chains: new Set(), tags: new Set(), customWebsite: false })
    setSearchQuery('')
    setOnlyFavorites(false)
  }, [])

  const activeFilterCount =
    (filters.withImages ? 1 : 0) +
    (filters.multipleImages ? 1 : 0) +
    (filters.customWebsite ? 1 : 0) +
    (onlyFavorites ? 1 : 0) +
    filters.providers.size +
    filters.chains.size +
    filters.tags.size

  const isFilterActive = activeFilterCount > 0

  const cafesInView = useMemo(() => filteredCafeMap.size, [filteredCafeMap])

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
          <button
            onClick={() => setOnlyFavorites(f => !f)}
            className={`px-3 py-1.5 rounded-full text-sm font-medium border transition-colors ${
              onlyFavorites
                ? 'bg-amber-500 text-white border-amber-500'
                : 'bg-white text-gray-600 border-gray-300 hover:border-amber-400'
            }`}
          >
            ⭐ Favorites{favorites.length > 0 ? ` (${favorites.length})` : ''}
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
        {showTransitLayer && (
          <TileLayer
            attribution='Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors | Style: &copy; <a href="https://www.OpenRailwayMap.org">OpenRailwayMap</a> (CC-BY-SA)'
            url="https://{s}.tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png"
            subdomains="abcd"
            maxZoom={19}
            opacity={0.7}
          />
        )}
        {showSubwayLayer && <SeoulSubwayLayer onLoading={setSubwayLoading} />}
        {!IS_PUBLIC && showScrapeCoverage && <ScrapeCoverageLayer onRollup={setCoverageRollup} />}
        <ViewportTracker onBoundsChange={handleBoundsChange} />
        <MapPositionSaver />
        <MapPanner target={mapTarget} />
        {!showHeatmap && <MarkerLayer scraped_cafes={filteredCafeMap} onSelect={handleSelect} showSourceColors={showSourceColors} showBrandColors={showBrandColors} showImageRing={showImageRing} favoriteIds={favoriteIds} />}
        {showHeatmap && <HeatmapLayer points={heatmapPoints} radiusMult={heatmapRadius / 10} />}
        <LocationDotLayer location={userLocation} />
        <ScaleControl position="bottomright" metric imperial={false} />
        <ScalePositioner />
        <RecentlyVisitedLayer visits={visits} selectedId={selectedId} visibleIds={filteredCafeIds} />
      </MapContainer>

      {/* Top left: Logo + Desktop search bar */}
      <div className="absolute top-2 left-2 z-[1100] flex items-center gap-2 pointer-events-auto">
        <button
          onClick={() => setShowAbout(true)}
          className="bg-white rounded-lg shadow px-3 py-1.5 text-base font-semibold text-gray-700 flex items-center gap-1.5 hover:bg-gray-50 transition-colors shrink-0"
        >
          <img src="/favicon.svg" alt="Workcafe Korea" className="h-6 w-6" />
          Workcafe Korea
        </button>
        <SearchBar
          query={searchQuery}
          setQuery={setSearchQuery}
          results={searchResults}
          totalCount={filteredCafeMap.size}
          onSelect={handleSearchSelect}
          className="hidden md:block w-64"
        />
      </div>

      {/* Mobile search bar */}
      <div className="absolute left-2 right-2 z-[600] flex md:hidden pointer-events-auto" style={{ top: '52px' }}>
        <SearchBar
          query={searchQuery}
          setQuery={setSearchQuery}
          results={searchResults}
          totalCount={filteredCafeMap.size}
          onSelect={handleSearchSelect}
          className="w-full"
        />
      </div>

      {/* Top right: Desktop buttons */}
      <div className="absolute top-2 right-2 z-[500] hidden md:flex items-center gap-2 pointer-events-auto">
        <button
          className={`relative rounded-lg shadow px-3 py-1.5 text-sm transition-colors ${showFilters ? 'bg-blue-600 text-white hover:bg-blue-700' : isFilterActive ? 'bg-white ring-2 ring-blue-400 text-blue-600 hover:bg-gray-50' : 'bg-white text-gray-700 hover:bg-gray-50'}`}
          onClick={() => setShowFilters(!showFilters)}
        >
          Filter
          {activeFilterCount > 0 && (
            <span className="absolute -top-1.5 -right-1.5 min-w-[18px] h-[18px] bg-blue-600 text-white text-[10px] rounded-full flex items-center justify-center font-bold px-1">
              {activeFilterCount}
            </span>
          )}
        </button>
        <button
          className={`bg-white rounded-lg shadow px-3 py-1.5 text-sm hover:bg-gray-50 transition-colors ${showMapSettings ? 'ring-2 ring-slate-400 text-slate-700' : (showHeatmap || showSourceColors || showBrandColors) ? 'ring-2 ring-slate-300 text-slate-600' : 'text-gray-500'}`}
          onClick={() => setShowMapSettings(v => !v)}
        >
          Map
        </button>
        <button
          className={`bg-white rounded-lg shadow px-3 py-1.5 text-sm hover:bg-gray-50 transition-colors ${showLayers ? 'ring-2 ring-indigo-400 text-indigo-700' : (showTransitLayer || showSubwayLayer) ? 'ring-2 ring-indigo-200 text-indigo-500' : 'text-gray-500'}`}
          onClick={() => setShowLayers(v => !v)}
          title="Overlay layers"
        >
          🚇 Layers
        </button>
        <button
          className={`bg-white rounded-lg shadow px-3 py-1.5 text-sm hover:bg-gray-50 transition-colors ${visits.length > 0 ? 'text-amber-600' : 'text-gray-400'}`}
          onClick={() => setShowRecently(r => !r)}
          title="Recently visited"
        >
          🕐 {visits.length > 0 ? visits.length : ''}
        </button>
        <button
          className={`bg-white rounded-lg shadow px-3 py-1.5 text-sm hover:bg-gray-50 transition-colors ${showFavorites ? 'ring-2 ring-amber-400' : ''} ${favorites.length > 0 ? 'text-amber-500' : 'text-gray-400'}`}
          onClick={() => setShowFavorites(r => !r)}
          title="Favorites"
        >
          ⭐ {favorites.length > 0 ? favorites.length : ''}
        </button>
        <button
          className="bg-white rounded-lg shadow px-3 py-1.5 text-sm hover:bg-gray-50 text-gray-500"
          onClick={() => setShowStats(true)}
        >
          Stats
        </button>
        <button
          className="bg-white rounded-lg shadow px-3 py-1.5 text-sm hover:bg-gray-50 text-gray-500"
          onClick={() => setShowAbout(true)}
          title="About"
        >
          ℹ️
        </button>
        {!IS_PUBLIC && (
          <>
            <button
              className="bg-white rounded-lg shadow px-3 py-1.5 text-sm hover:bg-gray-50 text-gray-500"
              onClick={() => setShowTagBrowser(true)}
            >
              Tags
            </button>
            <SnapshotSelector snapshot={snapshot} setSnapshot={setSnapshot} />
          </>
        )}
      </div>

      {/* Top right: Mobile Filter + Hamburger */}
      <div className="absolute top-2 right-2 z-[500] flex md:hidden items-center gap-2 pointer-events-auto">
        <button
          onClick={() => setShowFilters(true)}
          className={`relative bg-white rounded-full shadow w-9 h-9 flex items-center justify-center transition-colors ${isFilterActive ? 'ring-2 ring-blue-400 text-blue-600' : 'text-gray-600 hover:bg-gray-50'}`}
          title="Filters"
        >
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-5 h-5">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 0 1-.659 1.591L15.25 12.5v6.25a.75.75 0 0 1-.75.75h-5a.75.75 0 0 1-.75-.75V12.5L3.659 7.409A2.25 2.25 0 0 1 3 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0 1 12 3Z" />
          </svg>
          {activeFilterCount > 0 && (
            <span className="absolute -top-1 -right-1 min-w-[16px] h-4 bg-blue-600 text-white text-[9px] rounded-full flex items-center justify-center font-bold px-0.5">
              {activeFilterCount}
            </span>
          )}
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
      <div className="absolute right-2 z-[500] flex flex-col items-end gap-2 pointer-events-auto" style={{ bottom: 'calc(2.5rem + env(safe-area-inset-bottom, 0px))' }}>
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
      <div className="absolute right-[10px] z-[500] pointer-events-none" style={{ bottom: 'calc(18px + env(safe-area-inset-bottom, 0px))' }}>
        <div className="bg-white/95 rounded-xl shadow px-3 py-1.5 text-sm text-gray-600 font-medium flex items-center gap-2">
          {loading && (
            <svg className="w-3.5 h-3.5 animate-spin text-blue-400 shrink-0" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          )}
          {loading ? '…' : `${cafesInView.toLocaleString()} / ${total.toLocaleString()}`}
        </div>
      </div>

      {/* Bottom left: build info */}
      <button
        className="absolute left-2 z-[500] bg-white/80 rounded px-1.5 py-0.5 text-[10px] text-gray-400 font-mono hover:bg-white/95 transition-colors"
        style={{ bottom: 'calc(0.5rem + env(safe-area-inset-bottom, 0px))' }}
        onClick={() => setShowAbout(true)}
        title={`Built ${new Date(__BUILD_DATE__).toLocaleString()}`}
      >
        {__GIT_SHA__} · {new Date(__BUILD_DATE__).toLocaleDateString()}
      </button>

      {/* Desktop Map Settings panel */}
      {showMapSettings && !isMobile && <div className="fixed inset-0 z-[599]" onClick={() => setShowMapSettings(false)} />}
      {showMapSettings && !isMobile && (
        <div className="absolute top-14 right-3 z-[600] bg-white rounded-xl shadow-2xl w-64">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
            <span className="font-semibold text-sm text-gray-800">Map Settings</span>
            <button onClick={() => setShowMapSettings(false)} className="text-gray-400 hover:text-gray-600 text-lg w-6 h-6 flex items-center justify-center">✕</button>
          </div>
          <div className="p-3 flex flex-col gap-1">
            {/* Heatmap */}
            <label className="flex items-center justify-between px-2 py-2 rounded-lg hover:bg-gray-50 cursor-pointer select-none">
              <span className="text-sm text-gray-700">🌡️ Heatmap</span>
              <input type="checkbox" checked={showHeatmap} onChange={e => setShowHeatmap(e.target.checked)} className="w-4 h-4 accent-orange-500" />
            </label>
            {showHeatmap && (
              <div className="flex items-center gap-2 px-2 pb-1">
                <span className="text-xs text-gray-400 w-5 shrink-0">r</span>
                <input type="range" min={4} max={500} value={heatmapRadius} onChange={e => setHeatmapRadius(Number(e.target.value))} className="flex-1 accent-orange-500" />
                <span className="text-xs text-orange-500 font-mono w-8 text-right">{heatmapRadius}</span>
              </div>
            )}
            <div className="border-t border-gray-100 my-1" />
            {/* Source colors */}
            <label className="flex items-center justify-between px-2 py-2 rounded-lg hover:bg-gray-50 cursor-pointer select-none">
              <span className="text-sm text-gray-700">🗺 Source colors</span>
              <input type="checkbox" checked={showSourceColors} onChange={e => setShowSourceColors(e.target.checked)} className="w-4 h-4 accent-slate-500" />
            </label>
            {/* Brand colors */}
            <label className="flex items-center justify-between px-2 py-2 rounded-lg hover:bg-gray-50 cursor-pointer select-none">
              <span className="text-sm text-gray-700">🏷 Brand colors</span>
              <input type="checkbox" checked={showBrandColors} onChange={e => setShowBrandColors(e.target.checked)} className="w-4 h-4 accent-slate-500" />
            </label>
            {/* Image ring */}
            <label className="flex items-center justify-between px-2 py-2 rounded-lg hover:bg-gray-50 cursor-pointer select-none">
              <span className="text-sm text-gray-700">📷 Ring if has photos</span>
              <input type="checkbox" checked={showImageRing} onChange={e => setShowImageRing(e.target.checked)} className="w-4 h-4 accent-slate-500" />
            </label>
            {/* Provider legend — only when source colors on */}
            {showSourceColors && (
              <div className="mt-2 pt-2 border-t border-gray-100">
                <div className="text-xs text-gray-400 font-medium px-2 mb-1">Scraper sources</div>
                {Object.entries(PROVIDER_COLORS).map(([p, color]) => (
                  <div key={p} className="flex items-center gap-2 px-2 py-0.5">
                    <div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: color }} />
                    <span className="text-xs text-gray-600">{p}</span>
                  </div>
                ))}
                <div className="px-2 mt-1 text-[10px] text-gray-400">black ring = has images</div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Desktop Layers panel */}
      {showLayers && !isMobile && <div className="fixed inset-0 z-[599]" onClick={() => setShowLayers(false)} />}
      {showLayers && !isMobile && (
        <div className="absolute top-14 right-3 z-[600] bg-white rounded-xl shadow-2xl w-64">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
            <span className="font-semibold text-sm text-gray-800">Overlay Layers</span>
            <button onClick={() => setShowLayers(false)} className="text-gray-400 hover:text-gray-600 text-lg w-6 h-6 flex items-center justify-center">✕</button>
          </div>
          <div className="p-3 flex flex-col gap-1">
            <label className="flex items-center justify-between px-2 py-2 rounded-lg hover:bg-gray-50 cursor-pointer select-none">
              <div>
                <div className="text-sm text-gray-700">🚇 Subway / Railway</div>
                <div className="text-[10px] text-gray-400">OpenRailwayMap · OSM data</div>
              </div>
              <input type="checkbox" checked={showTransitLayer} onChange={e => setShowTransitLayer(e.target.checked)} className="w-4 h-4 accent-indigo-500" />
            </label>
            <label className="flex items-center justify-between px-2 py-2 rounded-lg hover:bg-gray-50 cursor-pointer select-none">
              <div>
                <div className="text-sm text-gray-700">
                  🗺 Seoul Metro (colored)
                  {showSubwayLayer && subwayLoading && <span className="ml-1 text-[10px] text-indigo-400 animate-pulse">loading…</span>}
                </div>
                <div className="text-[10px] text-gray-400">OpenStreetMap · Overpass API</div>
              </div>
              <input type="checkbox" checked={showSubwayLayer} onChange={e => setShowSubwayLayer(e.target.checked)} className="w-4 h-4 accent-indigo-500" />
            </label>
            {!IS_PUBLIC && (
              <label className="flex items-center justify-between px-2 py-2 rounded-lg hover:bg-gray-50 cursor-pointer select-none">
                <div>
                  <div className="text-sm text-gray-700">🐌 Scrape coverage</div>
                  <div className="text-[10px] text-gray-400">1km grid · heat by cafe count</div>
                </div>
                <input type="checkbox" checked={showScrapeCoverage} onChange={e => setShowScrapeCoverage(e.target.checked)} className="w-4 h-4 accent-rose-500" />
              </label>
            )}
            {!IS_PUBLIC && showScrapeCoverage && coverageRollup && (
              <div className="mx-2 mb-1 px-2.5 py-2 rounded-lg bg-gray-50 border border-gray-100">
                <div className="text-[10px] text-gray-500 mb-1">
                  {coverageRollup.cell_count} cells · {coverageRollup.total_cafes.toLocaleString()} cafes (in view)
                </div>
                <div className="flex flex-col gap-0.5">
                  {coverageRollup.per_provider.map(p => (
                    <div key={p.provider} className="flex items-center justify-between text-[11px]">
                      <span className="flex items-center gap-1.5 text-gray-600">
                        <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: PROVIDER_COLORS[p.provider] ?? '#9ca3af' }} />
                        {p.provider}
                      </span>
                      <span className="font-mono text-gray-500">{p.cafes.toLocaleString()} · {p.cells_complete}c</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Desktop recently visited panel */}
      {showRecently && !isMobile && (
        <div className="absolute top-14 right-3 z-[600] bg-white rounded-xl shadow-2xl w-72 max-h-[70vh] overflow-y-auto">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 sticky top-0 bg-white">
            <span className="font-semibold text-sm text-gray-800">Recently Visited</span>
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer select-none">
                <input type="checkbox" checked={onlyMostRecent} onChange={e => setOnlyMostRecent(e.target.checked)} className="w-3 h-3" />
                Only latest
              </label>
              {visits.length > 0 && (
                <button onClick={clearVisits} className="text-xs text-red-400 hover:text-red-600" title="Clear all history">Clear all</button>
              )}
              <button onClick={() => setShowRecently(false)} className="text-gray-400 hover:text-gray-600 text-lg w-6 h-6 flex items-center justify-center">✕</button>
            </div>
          </div>
          {visits.length === 0 ? (
            <div className="px-4 py-6 text-xs text-gray-400 text-center">No visits yet. Click a cafe to track it.</div>
          ) : (
            <div>
              {(onlyMostRecent ? visits.slice(0, 1) : visits).map(v => (
                <div
                  key={v.id}
                  className={`flex items-center gap-1 border-b border-gray-50 ${v.id === selectedId ? 'bg-amber-50' : ''}`}
                >
                  <button
                    onClick={() => { navigate(`/cafe/${v.id}`); setShowRecently(false) }}
                    className="flex-1 flex items-center gap-3 px-4 py-2.5 hover:bg-black/5 text-left min-w-0"
                  >
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-gray-800 truncate">{v.name}</div>
                      <div className="text-xs text-gray-400">{timeAgo(v.timestamp)}</div>
                    </div>
                  </button>
                  <button
                    onClick={() => removeVisit(v.id)}
                    className="shrink-0 px-2 py-2 text-gray-300 hover:text-gray-600 text-sm"
                    title="Remove from history"
                  >✕</button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Desktop favorites panel */}
      {showFavorites && !isMobile && (
        <div className="absolute top-14 right-3 z-[600] bg-white rounded-xl shadow-2xl w-72 max-h-[70vh] overflow-y-auto">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 sticky top-0 bg-white">
            <span className="font-semibold text-sm text-gray-800">⭐ Favorites</span>
            <div className="flex items-center gap-3">
              {favorites.length > 0 && (
                <button onClick={clearFavorites} className="text-xs text-red-400 hover:text-red-600" title="Clear all favorites">Clear all</button>
              )}
              <button onClick={() => setShowFavorites(false)} className="text-gray-400 hover:text-gray-600 text-lg w-6 h-6 flex items-center justify-center">✕</button>
            </div>
          </div>
          {favorites.length === 0 ? (
            <div className="px-4 py-6 text-xs text-gray-400 text-center">No favorites yet. Open a cafe and click ☆ to favorite it.</div>
          ) : (
            <div>
              {favorites.map(f => (
                <div
                  key={f.id}
                  className={`flex items-center gap-1 border-b border-gray-50 ${f.id === selectedId ? 'bg-amber-50' : ''}`}
                >
                  <button
                    onClick={() => { navigate(`/cafe/${f.id}`); setShowFavorites(false) }}
                    className="flex-1 flex items-center gap-3 px-4 py-2.5 hover:bg-black/5 text-left min-w-0"
                  >
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-gray-800 truncate">{f.name}</div>
                    </div>
                  </button>
                  <button
                    onClick={() => toggleFavorite({ id: f.id, name: f.name, lat: f.lat, lon: f.lon })}
                    className="shrink-0 px-2 py-2 text-amber-400 hover:text-gray-400 text-sm"
                    title="Remove from favorites"
                  >⭐</button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Desktop filter panel */}
      {showFilters && !isMobile && <div className="fixed inset-0 z-[599]" onClick={() => setShowFilters(false)} />}
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
                <button
                  onClick={() => setOnlyFavorites(f => !f)}
                  className={`px-3 py-1.5 rounded-full text-sm font-medium border transition-colors text-left ${
                    onlyFavorites
                      ? 'bg-amber-500 text-white border-amber-500'
                      : 'bg-white text-gray-600 border-gray-300 hover:border-amber-400'
                  }`}
                >
                  ⭐ Favorites{favorites.length > 0 ? ` (${favorites.length})` : ''}
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
              <span className="font-semibold text-gray-800 flex items-center gap-1.5"><img src="/favicon.svg" alt="" className="h-5 w-5" />Workcafe Korea</span>
              <button onClick={() => setShowMobileMenu(false)} className="text-gray-400 hover:text-gray-600 text-xl w-8 h-8 flex items-center justify-center">✕</button>
            </div>
            <div className="flex-1 overflow-y-auto">
              {/* Map Settings section */}
              <button
                onClick={() => setShowMapSettings(v => !v)}
                className={`w-full flex items-center gap-4 px-5 py-4 hover:bg-gray-50 border-b border-gray-100 text-left ${showMapSettings ? 'bg-slate-50' : ''}`}
              >
                <span className="text-xl w-7">🗺</span>
                <span className={`text-sm font-medium ${showMapSettings ? 'text-slate-700' : 'text-gray-700'}`}>
                  Map {(showHeatmap || showSourceColors || showBrandColors) ? '●' : ''}
                </span>
              </button>
              {showMapSettings && (
                <div className="border-b border-gray-100 bg-slate-50 px-5 py-3 flex flex-col gap-2">
                  <label className="flex items-center justify-between text-sm text-gray-700 cursor-pointer select-none">
                    <span>🌡️ Heatmap</span>
                    <input type="checkbox" checked={showHeatmap} onChange={e => setShowHeatmap(e.target.checked)} className="w-4 h-4 accent-orange-500" />
                  </label>
                  {showHeatmap && (
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-gray-400 w-5">r</span>
                      <input type="range" min={4} max={500} value={heatmapRadius} onChange={e => setHeatmapRadius(Number(e.target.value))} className="flex-1 accent-orange-500" />
                      <span className="text-xs text-orange-500 font-mono w-8 text-right">{heatmapRadius}</span>
                    </div>
                  )}
                  <label className="flex items-center justify-between text-sm text-gray-700 cursor-pointer select-none">
                    <span>🗺 Source colors</span>
                    <input type="checkbox" checked={showSourceColors} onChange={e => setShowSourceColors(e.target.checked)} className="w-4 h-4 accent-slate-500" />
                  </label>
                  <label className="flex items-center justify-between text-sm text-gray-700 cursor-pointer select-none">
                    <span>🏷 Brand colors</span>
                    <input type="checkbox" checked={showBrandColors} onChange={e => setShowBrandColors(e.target.checked)} className="w-4 h-4 accent-slate-500" />
                  </label>
                  <label className="flex items-center justify-between text-sm text-gray-700 cursor-pointer select-none">
                    <span>📷 Ring if has photos</span>
                    <input type="checkbox" checked={showImageRing} onChange={e => setShowImageRing(e.target.checked)} className="w-4 h-4 accent-slate-500" />
                  </label>
                  {showSourceColors && (
                    <div className="pt-1 border-t border-gray-200">
                      {Object.entries(PROVIDER_COLORS).map(([p, color]) => (
                        <div key={p} className="flex items-center gap-2 py-0.5">
                          <div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: color }} />
                          <span className="text-xs text-gray-600">{p}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {/* Layers section */}
              <button
                onClick={() => setShowLayers(v => !v)}
                className={`w-full flex items-center gap-4 px-5 py-4 hover:bg-gray-50 border-b border-gray-100 text-left ${showLayers ? 'bg-indigo-50' : ''}`}
              >
                <span className="text-xl w-7">🚇</span>
                <span className={`text-sm font-medium ${showLayers ? 'text-indigo-700' : 'text-gray-700'}`}>
                  Layers {(showTransitLayer || showSubwayLayer) ? '●' : ''}
                </span>
              </button>
              {showLayers && (
                <div className="border-b border-gray-100 bg-indigo-50 px-5 py-3 flex flex-col gap-2">
                  <label className="flex items-center justify-between text-sm text-gray-700 cursor-pointer select-none">
                    <div>
                      <div>🚇 Subway / Railway</div>
                      <div className="text-[10px] text-gray-400">OpenRailwayMap · OSM data</div>
                    </div>
                    <input type="checkbox" checked={showTransitLayer} onChange={e => setShowTransitLayer(e.target.checked)} className="w-4 h-4 accent-indigo-500" />
                  </label>
                  <label className="flex items-center justify-between text-sm text-gray-700 cursor-pointer select-none">
                    <div>
                      <div>
                        🗺 Seoul Metro (colored)
                        {showSubwayLayer && subwayLoading && <span className="ml-1 text-[10px] text-indigo-400 animate-pulse">loading…</span>}
                      </div>
                      <div className="text-[10px] text-gray-400">OpenStreetMap · Overpass API</div>
                    </div>
                    <input type="checkbox" checked={showSubwayLayer} onChange={e => setShowSubwayLayer(e.target.checked)} className="w-4 h-4 accent-indigo-500" />
                  </label>
                  {!IS_PUBLIC && (
                    <label className="flex items-center justify-between text-sm text-gray-700 cursor-pointer select-none">
                      <div>
                        <div>🐌 Scrape coverage</div>
                        <div className="text-[10px] text-gray-400">1km grid · heat by cafe count</div>
                      </div>
                      <input type="checkbox" checked={showScrapeCoverage} onChange={e => setShowScrapeCoverage(e.target.checked)} className="w-4 h-4 accent-rose-500" />
                    </label>
                  )}
                </div>
              )}
              {/* Recently visited section */}
              <button
                onClick={() => setShowRecently(r => !r)}
                className={`w-full flex items-center gap-4 px-5 py-4 hover:bg-gray-50 border-b border-gray-100 text-left ${showRecently ? 'bg-amber-50' : ''}`}
              >
                <span className="text-xl w-7">🕐</span>
                <span className={`text-sm font-medium ${showRecently ? 'text-amber-600' : 'text-gray-700'}`}>
                  Recently {visits.length > 0 ? `(${visits.length})` : ''}
                </span>
              </button>
              {showRecently && (
                <div className="border-b border-gray-100">
                  <div className="flex items-center justify-between px-5 py-2 bg-amber-50">
                    <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer select-none">
                      <input type="checkbox" checked={onlyMostRecent} onChange={e => setOnlyMostRecent(e.target.checked)} className="w-3 h-3" />
                      Only latest
                    </label>
                    {visits.length > 0 && (
                      <button onClick={clearVisits} className="text-xs text-red-400 hover:text-red-600">Clear all</button>
                    )}
                  </div>
                  {visits.length === 0 ? (
                    <div className="px-5 py-3 text-xs text-gray-400">No visits yet.</div>
                  ) : (
                    (onlyMostRecent ? visits.slice(0, 1) : visits).map(v => (
                      <div
                        key={v.id}
                        className={`flex items-center border-t border-gray-50 ${v.id === selectedId ? 'bg-amber-50' : ''}`}
                      >
                        <button
                          onClick={() => { navigate(`/cafe/${v.id}`); setShowMobileMenu(false); setShowRecently(false) }}
                          className="flex-1 flex items-center gap-3 px-5 py-2.5 hover:bg-black/5 text-left min-w-0"
                        >
                          <div className="min-w-0">
                            <div className="text-sm font-medium text-gray-800 truncate">{v.name}</div>
                            <div className="text-xs text-gray-400">{timeAgo(v.timestamp)}</div>
                          </div>
                        </button>
                        <button
                          onClick={() => removeVisit(v.id)}
                          className="shrink-0 px-3 py-2 text-gray-300 hover:text-gray-600 text-sm"
                          title="Remove"
                        >✕</button>
                      </div>
                    ))
                  )}
                </div>
              )}
              {/* Favorites section */}
              <button
                onClick={() => setShowFavorites(r => !r)}
                className={`w-full flex items-center gap-4 px-5 py-4 hover:bg-gray-50 border-b border-gray-100 text-left ${showFavorites ? 'bg-amber-50' : ''}`}
              >
                <span className="text-xl w-7">⭐</span>
                <span className={`text-sm font-medium ${showFavorites ? 'text-amber-600' : 'text-gray-700'}`}>
                  Favorites {favorites.length > 0 ? `(${favorites.length})` : ''}
                </span>
              </button>
              {showFavorites && (
                <div className="border-b border-gray-100">
                  <div className="flex items-center justify-between px-5 py-2 bg-amber-50">
                    <span className="text-xs text-gray-500">Saved cafes</span>
                    {favorites.length > 0 && (
                      <button onClick={clearFavorites} className="text-xs text-red-400 hover:text-red-600">Clear all</button>
                    )}
                  </div>
                  {favorites.length === 0 ? (
                    <div className="px-5 py-3 text-xs text-gray-400">No favorites yet.</div>
                  ) : (
                    favorites.map(f => (
                      <div
                        key={f.id}
                        className={`flex items-center border-t border-gray-50 ${f.id === selectedId ? 'bg-amber-50' : ''}`}
                      >
                        <button
                          onClick={() => { navigate(`/cafe/${f.id}`); setShowMobileMenu(false); setShowFavorites(false) }}
                          className="flex-1 flex items-center gap-3 px-5 py-2.5 hover:bg-black/5 text-left min-w-0"
                        >
                          <div className="min-w-0">
                            <div className="text-sm font-medium text-gray-800 truncate">{f.name}</div>
                          </div>
                        </button>
                        <button
                          onClick={() => toggleFavorite({ id: f.id, name: f.name, lat: f.lat, lon: f.lon })}
                          className="shrink-0 px-3 py-2 text-amber-400 hover:text-gray-400 text-sm"
                          title="Remove"
                        >⭐</button>
                      </div>
                    ))
                  )}
                </div>
              )}
              <button
                onClick={() => { setShowMobileMenu(false); setShowStats(true) }}
                className="w-full flex items-center gap-4 px-5 py-4 hover:bg-gray-50 border-b border-gray-100 text-left"
              >
                <span className="text-xl w-7">📊</span>
                <span className="text-sm font-medium text-gray-700">Stats</span>
              </button>
              <button
                onClick={() => { setShowMobileMenu(false); setShowAbout(true) }}
                className="w-full flex items-center gap-4 px-5 py-4 hover:bg-gray-50 border-b border-gray-100 text-left"
              >
                <span className="text-xl w-7">ℹ️</span>
                <span className="text-sm font-medium text-gray-700">About</span>
              </button>
              {!IS_PUBLIC && (
                <>
                  <button
                    onClick={() => { setShowMobileMenu(false); setShowTagBrowser(true) }}
                    className="w-full flex items-center gap-4 px-5 py-4 hover:bg-gray-50 border-b border-gray-100 text-left"
                  >
                    <span className="text-xl w-7">🏷️</span>
                    <span className="text-sm font-medium text-gray-700">Tag Browser</span>
                  </button>
                  <button
                    onClick={() => { setShowMobileMenu(false); setShowSettings(true) }}
                    className="w-full flex items-center gap-4 px-5 py-4 hover:bg-gray-50 border-b border-gray-100 text-left"
                  >
                    <span className="text-xl w-7">🔧</span>
                    <span className="text-sm font-medium text-gray-700">Scraper Status</span>
                  </button>
                  <div className="px-5 py-4 border-b border-gray-100">
                    <div className="flex items-center gap-4 mb-3">
                      <span className="text-xl w-7">🕐</span>
                      <span className="text-sm font-medium text-gray-700">DB Snapshot</span>
                    </div>
                    <SnapshotSelector snapshot={snapshot} setSnapshot={setSnapshot} />
                  </div>
                </>
              )}
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
          isFavorite={favoriteIds.has(selectedId)}
          onToggleFavorite={toggleFavorite}
        />
      )}

      {selectedId && location.search && (
        <CafeDetailsPage activeTags={filters.tags} />
      )}

      {!IS_PUBLIC && showSettings && (
        <SettingsModal onClose={() => setShowSettings(false)} showToggles={IS_DEVMODE} />
      )}

      {showStats && (
        <SettingsModal onClose={() => setShowStats(false)} showToggles={IS_DEVMODE} />
      )}

      {showTagBrowser && (
        <TagBrowserOverlay onClose={() => setShowTagBrowser(false)} />
      )}

      {showAbout && (
        <AboutModal onClose={() => setShowAbout(false)} />
      )}

    </div>
  )
}
