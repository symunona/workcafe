import { useEffect, useRef, useCallback } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'
import { PROVIDER_COLORS } from '../utils'

// Per-provider coverage in a single 1km grid cell.
interface CoverageProvider {
  status?: string // "completed" if a progress row exists for this cell
  cafes: number
}

interface CoverageCell {
  grid_x: number
  grid_y: number
  bbox: [number, number, number, number] // [minLat, minLon, maxLat, maxLon]
  providers: Record<string, CoverageProvider>
  total_cafes: number
}

interface CoverageRollup {
  provider: string
  cafes: number
  cells_complete: number
}

export interface CoverageResponse {
  cells: CoverageCell[]
  cell_count: number
  total_cafes: number
  per_provider: CoverageRollup[]
}

// Heat color by cafe count: grey (none) → pale blue → warm orange → deep red.
function heatColor(n: number): string {
  if (n <= 0) return '#9ca3af' // grey
  // log scale so a handful of cafes is already visible, saturating ~120/cell.
  const t = Math.min(1, Math.log10(n + 1) / Math.log10(120))
  // interpolate cold→hot through a small palette
  const stops: [number, [number, number, number]][] = [
    [0.0, [191, 219, 254]], // pale blue
    [0.35, [250, 204, 21]], // yellow
    [0.7, [249, 115, 22]], // orange
    [1.0, [220, 38, 38]], // red
  ]
  let lo = stops[0]
  let hi = stops[stops.length - 1]
  for (let i = 0; i < stops.length - 1; i++) {
    if (t >= stops[i][0] && t <= stops[i + 1][0]) {
      lo = stops[i]
      hi = stops[i + 1]
      break
    }
  }
  const span = hi[0] - lo[0] || 1
  const f = (t - lo[0]) / span
  const c = (a: number, b: number) => Math.round(a + (b - a) * f)
  return `rgb(${c(lo[1][0], hi[1][0])},${c(lo[1][1], hi[1][1])},${c(lo[1][2], hi[1][2])})`
}

// Corner placement: kakao TL, google TR, naver BL, osm BR.
const CORNERS = [
  { key: 'kakao', pos: 'tl' },
  { key: 'google', pos: 'tr' },
  { key: 'naver', pos: 'bl' },
  { key: 'osm', pos: 'br' },
] as const

function cornerLabelHtml(cell: CoverageCell): string {
  const dots = CORNERS.map(({ key, pos }) => {
    const p = cell.providers[key]
    if (!p) return ''
    const n = p.cafes
    const done = p.status === 'completed'
    const color = PROVIDER_COLORS[key] ?? '#6b7280'
    // ✓ overlay-style: bold if completed, dim if only cafes present.
    const txt = n > 0 ? String(n) : done ? '·' : ''
    if (!txt) return ''
    return `<span class="wc-cov-corner wc-cov-${pos}" style="color:${color};${done ? 'font-weight:700;' : 'opacity:0.75;'}">${txt}</span>`
  })
  return dots.join('')
}

export function ScrapeCoverageLayer({ onRollup }: { onRollup?: (r: CoverageResponse | null) => void }) {
  const map = useMap()
  const rectLayerRef = useRef<L.LayerGroup | null>(null)
  const labelLayerRef = useRef<L.LayerGroup | null>(null)
  const dataRef = useRef<CoverageCell[]>([])
  const abortRef = useRef<AbortController | null>(null)
  const onRollupRef = useRef(onRollup)
  onRollupRef.current = onRollup

  const drawLabels = useCallback(() => {
    const layer = labelLayerRef.current
    if (!layer) return
    layer.clearLayers()
    if (map.getZoom() < 14) return
    const bounds = map.getBounds()
    for (const cell of dataRef.current) {
      const [minLat, minLon, maxLat, maxLon] = cell.bbox
      const center = L.latLng((minLat + maxLat) / 2, (minLon + maxLon) / 2)
      if (!bounds.contains(center)) continue
      const html = cornerLabelHtml(cell)
      if (!html) continue
      const icon = L.divIcon({
        className: 'wc-cov-label',
        html: `<div class="wc-cov-label-box">${html}</div>`,
        iconSize: [0, 0],
        iconAnchor: [0, 0],
      })
      L.marker(center, { icon, interactive: false, keyboard: false }).addTo(layer)
    }
  }, [map])

  const drawRects = useCallback(() => {
    const layer = rectLayerRef.current
    if (!layer) return
    layer.clearLayers()
    const bounds = map.getBounds().pad(0.2)
    for (const cell of dataRef.current) {
      const [minLat, minLon, maxLat, maxLon] = cell.bbox
      const cellBounds = L.latLngBounds([minLat, minLon], [maxLat, maxLon])
      if (!bounds.intersects(cellBounds)) continue
      const fill = heatColor(cell.total_cafes)
      L.rectangle(cellBounds, {
        color: '#374151', // solid dark border
        weight: 1,
        opacity: 0.55,
        fillColor: fill,
        fillOpacity: cell.total_cafes > 0 ? 0.35 : 0.18,
        interactive: false,
      }).addTo(layer)
    }
    drawLabels()
  }, [map, drawLabels])

  const fetchCoverage = useCallback(() => {
    if (abortRef.current) abortRef.current.abort()
    abortRef.current = new AbortController()
    const b = map.getBounds()
    const params = new URLSearchParams({
      minLat: String(b.getSouth()),
      maxLat: String(b.getNorth()),
      minLon: String(b.getWest()),
      maxLon: String(b.getEast()),
    })
    fetch(`/api/scrape-coverage?${params}`, { signal: abortRef.current.signal })
      .then(r => (r.ok ? r.json() : null))
      .then((data: CoverageResponse | null) => {
        if (!data) return
        dataRef.current = data.cells ?? []
        onRollupRef.current?.(data)
        drawRects()
      })
      .catch(() => {})
  }, [map, drawRects])

  useEffect(() => {
    rectLayerRef.current = L.layerGroup().addTo(map)
    labelLayerRef.current = L.layerGroup().addTo(map)
    fetchCoverage()
    const onMove = () => fetchCoverage()
    const onZoom = () => drawLabels()
    map.on('moveend', onMove)
    map.on('zoomend', onZoom)
    return () => {
      map.off('moveend', onMove)
      map.off('zoomend', onZoom)
      rectLayerRef.current?.remove()
      labelLayerRef.current?.remove()
      rectLayerRef.current = null
      labelLayerRef.current = null
      abortRef.current?.abort()
      onRollupRef.current?.(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map])

  return null
}
