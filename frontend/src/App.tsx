import { useEffect, useRef, useState } from 'react'
import { MapContainer, TileLayer, CircleMarker, Popup } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import type { Cafe, NaverMetadata, KakaoMetadata, GoogleMetadata } from './types'
import './App.css'

const PROVIDER_COLORS: Record<string, string> = {
  naver:      '#7c3aed', // purple
  osm:        '#0ea5e9', // sky blue
  google:     '#ea4335', // google red
  kakao:      '#f59e0b', // amber
  foursquare: '#10b981', // emerald
}

function providerColor(provider: string) {
  return PROVIDER_COLORS[provider] ?? '#6b7280'
}

interface Filters {
  openNow: boolean
  withImages: boolean
  multipleImages: boolean
  providers: Set<string>
}

function getMeta(cafe: Cafe): NaverMetadata | null {
  if (!cafe.metadata || cafe.provider !== 'naver') return null
  return cafe.metadata as unknown as NaverMetadata
}

function getKakaoMeta(cafe: Cafe): KakaoMetadata | null {
  if (!cafe.metadata || cafe.provider !== 'kakao') return null
  return cafe.metadata as unknown as KakaoMetadata
}


function getGoogleMeta(cafe: Cafe): GoogleMetadata | null {
  if (!cafe.metadata || cafe.provider !== 'google') return null
  return cafe.metadata as unknown as GoogleMetadata
}

function getImages(cafe: Cafe): string[] {
  // Always prefer locally hosted images when available
  const anyMeta = cafe.metadata as { local_images?: string[], local_image_paths?: string[] } | null
  if (anyMeta?.local_images?.length) return anyMeta.local_images
  if (anyMeta?.local_image_paths?.length) {
    // Convert relative path like "../data/seoul/naver/2025551608/image_0.jpg" to valid URLs
    // Or if they are already valid URLs, just use them
    return anyMeta.local_image_paths.map(p => {
      if (p.startsWith('../data/seoul/')) {
        // Nginx is serving /images -> /home/symunona/dev/workcafe/data/seoul
        return p.replace('../data/seoul/', '/images/')
      }
      return p
    })
  }

  // Fall back to CDN URLs until scraper has downloaded them
  const naver = getMeta(cafe)
  if (naver) {
    return naver.thumUrls?.length ? naver.thumUrls : naver.thumUrl ? [naver.thumUrl] : []
  }
  const kakao = getKakaoMeta(cafe)
  if (kakao) {
    const urls = kakao.image_info?.image_main_urls
    if (urls?.length) return urls
    if (kakao.img) return [kakao.img]
  }
  const google = getGoogleMeta(cafe)
  if (google) {
    if (google.local_images?.length) return google.local_images
  }
  return []
}

function isOpenNow(cafe: Cafe): boolean {
  const meta = getMeta(cafe)
  return meta?.businessStatus?.status?.code === 2
}

function hasImage(cafe: Cafe): boolean {
  return getImages(cafe).length > 0
}

function hasMultipleImages(cafe: Cafe): boolean {
  return getImages(cafe).length > 1
}

function imageCount(cafes: Cafe[]): number {
  return cafes.filter(hasImage).length
}

function multiImgCount(cafes: Cafe[]): number {
  return cafes.filter(hasMultipleImages).length
}

function ImageCarousel({ images, alt }: { images: string[], alt: string }) {
  const [idx, setIdx] = useState(0)
  if (images.length === 0) return null
  if (images.length === 1) return (
    <div className="cafe-popup-img">
      <img src={images[0]} alt={alt} />
    </div>
  )
  return (
    <div className="cafe-popup-img carousel">
      <img src={images[idx]} alt={alt} />
      <button className="carousel-btn prev" onClick={e => { e.stopPropagation(); setIdx(i => (i - 1 + images.length) % images.length) }}>‹</button>
      <button className="carousel-btn next" onClick={e => { e.stopPropagation(); setIdx(i => (i + 1) % images.length) }}>›</button>
      <div className="carousel-counter">{idx + 1} / {images.length}</div>
    </div>
  )
}

function CafePopup({ cafe }: { cafe: Cafe }) {
  const meta = getMeta(cafe)
  const images = getImages(cafe)
  const categories = meta?.category ?? []
  const status = meta?.businessStatus?.status
  const isOpen = status?.code === 2

  return (
    <div className="cafe-popup">
      <ImageCarousel images={images} alt={cafe.name} />
      <div className="cafe-popup-body">
        <div className="cafe-popup-name">{cafe.name}</div>
        {categories.length > 0 && (
          <div className="cafe-popup-tags">
            {categories.slice(0, 3).map(cat => (
              <span key={cat} className="cafe-popup-tag">{cat}</span>
            ))}
          </div>
        )}
        <div className="cafe-popup-rows">
          {status && (
            <div className={`cafe-popup-status ${isOpen ? 'open' : 'closed'}`}>
              <span className="status-dot" />
              {status.text}
              {status.description && <span className="status-desc"> · {status.description}</span>}
            </div>
          )}
          {meta?.tel && <div className="cafe-popup-row"><PhoneIcon />{meta.tel}</div>}
          {cafe.address && <div className="cafe-popup-row"><PinIcon />{cafe.address}</div>}
          {meta?.reviewCount != null && (
            <div className="cafe-popup-row"><StarIcon />{meta.reviewCount.toLocaleString()} reviews</div>
          )}
        </div>
        {cafe.url && (
          <a href={cafe.url} target="_blank" rel="noopener noreferrer" className="cafe-popup-link">
            View on map
            <ArrowIcon />
          </a>
        )}
      </div>
    </div>
  )
}

function PhoneIcon() {
  return (
    <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z" />
    </svg>
  )
}

function PinIcon() {
  return (
    <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
    </svg>
  )
}

function StarIcon() {
  return (
    <svg width="12" height="12" fill="currentColor" viewBox="0 0 24 24">
      <path d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z" />
    </svg>
  )
}

function ArrowIcon() {
  return (
    <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
    </svg>
  )
}

export default function App() {
  const [cafes, setCafes] = useState<Cafe[]>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [showFilters, setShowFilters] = useState(false)
  const [filters, setFilters] = useState<Filters>({ openNow: false, withImages: false, multipleImages: false, providers: new Set() })
  const filterRef = useRef<HTMLDivElement>(null)

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
          >
            <Popup maxWidth={300} className="cafe-popup-wrapper">
              <CafePopup cafe={c} />
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>

      {/* Search bar overlay */}
      <div
        ref={filterRef}
        className="absolute top-5 left-1/2 -translate-x-1/2 z-[1000] w-full max-w-xl px-4"
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
              <span>Open now</span>
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
                  {availableProviders.map(p => (
                    <button
                      key={p}
                      onClick={() => toggleProvider(p)}
                      className={`chip ${filters.providers.has(p) ? 'active' : ''}`}
                      style={filters.providers.has(p) ? { background: providerColor(p), borderColor: providerColor(p) } : {}}
                    >
                      <span className="chip-dot" style={{ background: providerColor(p) }} />
                      {p}
                    </button>
                  ))}
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
    </div>
  )
}
