import { useEffect, useRef, useState } from 'react'
import { GeoJSON, CircleMarker, Tooltip } from 'react-leaflet'

const OVERPASS_URL = 'https://overpass-api.de/api/interpreter'
const CACHE_KEY = 'wc_seoul_subway_v2'

// Fetch route relations (line geometry) + station nodes in one request
const QUERY = `[out:json][timeout:60][bbox:37.41,126.73,37.72,127.18];
(
  relation["route"="subway"];
  node["railway"="station"]["subway"="yes"];
);
out geom;`

const LINE_COLORS: Record<string, string> = {
  '1': '#0052A4', '2': '#00A84D', '3': '#EF7C1C',
  '4': '#00A5DE', '5': '#996CAC', '6': '#CD7C2F',
  '7': '#747F00', '8': '#E6186C', '9': '#BDB092',
  'A': '#0090D2', 'B': '#F5A200', 'K': '#179B48',
  'S': '#77C4A3',
}

interface Station {
  lat: number
  lon: number
  name: string
  nameEn: string
}

interface SubwayData {
  lines: any
  stations: Station[]
}

function buildData(raw: any): SubwayData {
  const features: any[] = []
  const stations: Station[] = []

  for (const el of raw.elements ?? []) {
    if (el.type === 'relation') {
      const ref = el.tags?.ref || ''
      const color = el.tags?.colour || el.tags?.color || LINE_COLORS[ref] || '#888'
      const name = el.tags?.['name:en'] || el.tags?.name || ''
      const coords: [number, number][][] = []
      for (const m of el.members ?? []) {
        if (m.type === 'way' && Array.isArray(m.geometry) && m.geometry.length > 1) {
          coords.push(m.geometry.map((p: { lon: number; lat: number }) => [p.lon, p.lat]))
        }
      }
      if (coords.length) {
        features.push({
          type: 'Feature',
          properties: { name, color },
          geometry: { type: 'MultiLineString', coordinates: coords },
        })
      }
    } else if (el.type === 'node' && el.tags?.railway === 'station') {
      stations.push({
        lat: el.lat,
        lon: el.lon,
        name: el.tags.name || '',
        nameEn: el.tags['name:en'] || el.tags.name || '',
      })
    }
  }

  return { lines: { type: 'FeatureCollection', features }, stations }
}

export function SeoulSubwayLayer({ onLoading }: { onLoading?: (v: boolean) => void }) {
  const [data, setData] = useState<SubwayData | null>(null)
  const onLoadingRef = useRef(onLoading)
  onLoadingRef.current = onLoading

  useEffect(() => {
    let alive = true
    const cached = sessionStorage.getItem(CACHE_KEY)
    if (cached) {
      try { setData(JSON.parse(cached)); return } catch {}
    }
    onLoadingRef.current?.(true)
    fetch(OVERPASS_URL, {
      method: 'POST',
      body: new URLSearchParams({ data: QUERY }),
    })
      .then(r => r.json())
      .then(raw => {
        if (!alive) return
        const d = buildData(raw)
        sessionStorage.setItem(CACHE_KEY, JSON.stringify(d))
        setData(d)
      })
      .catch(() => {})
      .finally(() => { if (alive) onLoadingRef.current?.(false) })
    return () => { alive = false }
  }, [])

  if (!data) return null
  return (
    <>
      <GeoJSON
        key={data.lines.features?.length ?? 0}
        data={data.lines}
        style={(feature: any) => ({
          color: feature?.properties?.color ?? '#666',
          weight: 3,
          opacity: 0.55,
        })}
      />
      {data.stations.map((s, i) => (
        <CircleMarker
          key={i}
          center={[s.lat, s.lon]}
          radius={5}
          pathOptions={{ color: '#333', weight: 1.5, fillColor: '#fff', fillOpacity: 1 }}
        >
          <Tooltip direction="top" offset={[0, -6]}>
            <span style={{ fontSize: 12 }}>{s.nameEn || s.name}</span>
          </Tooltip>
        </CircleMarker>
      ))}
    </>
  )
}
