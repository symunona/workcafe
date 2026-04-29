import { useEffect, useState, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import type { CleanCafe, ImageInfo, SourceCafe } from '../types'
import { PROVIDER_COLORS } from '../utils'
import { useSnapshot } from './SnapshotSelector'

interface Props {
  cafeId: string
  onClose: () => void
  activeTags?: Set<string>
  starredTags?: Set<string>
  isMobile?: boolean
}

const PROVIDER_MAP_URL: Record<string, (lat: number, lon: number, name: string, src?: SourceCafe) => string> = {
  naver: (lat, lon, name, src) => src?.url || `https://map.naver.com/v5/search/${encodeURIComponent(name)}?c=${lon},${lat},15,0,0,0,dh`,
  kakao: (lat, lon, name, src) => src?.url || `https://map.kakao.com/link/map/${encodeURIComponent(name)},${lat},${lon}`,
  google: (lat, lon, _name, src) => src?.url || `https://www.google.com/maps/search/?api=1&query=${lat},${lon}`,
}

const PROVIDER_LABEL: Record<string, string> = {
  naver: 'Naver Maps',
  kakao: 'Kakao Maps',
  google: 'Google Maps',
}

export function CleanCafeDetailsPane({ cafeId, onClose, activeTags, starredTags, isMobile }: Props) {
  const [cafe, setCafe] = useState<CleanCafe | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [tagFilter, setTagFilter] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null)
  const navigate = useNavigate()
  const { snapshot, apiUrl } = useSnapshot()

  useEffect(() => {
    setLoading(true)
    setCafe(null)
    setError(false)
    setTagFilter(null)
    setExpanded(false)
    setLightboxIndex(null)
    fetch(apiUrl(`/api/clean_cafe?id=${encodeURIComponent(cafeId)}`))
      .then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json() })
      .then(data => { setCafe(data); setLoading(false) })
      .catch(() => { setError(true); setLoading(false) })
  }, [cafeId, snapshot]) // eslint-disable-line react-hooks/exhaustive-deps

  const allImages = cafe?.all_images ?? []

  const tagCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const img of allImages) {
      for (const t of img.tags ?? []) counts.set(t.tag, (counts.get(t.tag) ?? 0) + 1)
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [allImages])

  const sortedTagCounts = useMemo(() => [...tagCounts].sort(([tagA, cntA], [tagB, cntB]) => {
    const aScore = (activeTags?.has(tagA) ? 2 : 0) + (starredTags?.has(tagA) ? 1 : 0)
    const bScore = (activeTags?.has(tagB) ? 2 : 0) + (starredTags?.has(tagB) ? 1 : 0)
    return bScore !== aScore ? bScore - aScore : cntB - cntA
  }), [tagCounts, activeTags, starredTags])

  const sortedImages = useMemo(() => {
    const allTagsSet = new Set([...(activeTags ?? []), ...(starredTags ?? [])])
    if (allTagsSet.size) {
      const activeHit = activeTags?.size ? allImages.filter(img => img.tags?.some(t => activeTags.has(t.tag))) : []
      const starredHit = starredTags?.size ? allImages.filter(img => !activeHit.includes(img) && img.tags?.some(t => starredTags.has(t.tag))) : []
      const rest = allImages.filter(img => !activeHit.includes(img) && !starredHit.includes(img))
      if (activeTags?.size) activeHit.sort((a, b) => {
        const sa = Math.max(0, ...(a.tags?.filter(t => activeTags.has(t.tag)).map(t => t.score) ?? [0]))
        const sb = Math.max(0, ...(b.tags?.filter(t => activeTags.has(t.tag)).map(t => t.score) ?? [0]))
        return sb - sa
      })
      if (starredTags?.size) starredHit.sort((a, b) => {
        const sa = Math.max(0, ...(a.tags?.filter(t => starredTags.has(t.tag)).map(t => t.score) ?? [0]))
        const sb = Math.max(0, ...(b.tags?.filter(t => starredTags.has(t.tag)).map(t => t.score) ?? [0]))
        return sb - sa
      })
      return [...activeHit, ...starredHit, ...rest]
    }
    return [...allImages.filter(img => img.tags?.length), ...allImages.filter(img => !img.tags?.length)]
  }, [allImages, activeTags, starredTags])

  const displayImages = useMemo(() =>
    tagFilter ? sortedImages.filter(img => img.tags?.some(t => t.tag === tagFilter)) : sortedImages,
    [sortedImages, tagFilter]
  )

  const imageCountByProvider = useMemo(() => {
    const counts = new Map<string, number>()
    for (const img of allImages) counts.set(img.provider, (counts.get(img.provider) ?? 0) + 1)
    return [...counts.entries()]
  }, [allImages])

  let tel = '', bizhour = '', homePage = ''
  if (cafe?.sources) {
    const naver = cafe.sources.find(s => s.provider === 'naver')
    const kakao = cafe.sources.find(s => s.provider === 'kakao')
    if (naver?.metadata) {
      const m = naver.metadata as any
      tel = m.tel || m.telDisplay || ''
      bizhour = m.bizhourInfo || ''
      homePage = m.homePage || m.website || ''
    }
    if (kakao?.metadata) {
      const m = kakao.metadata as any
      if (!tel) tel = m.phone || ''
      if (!homePage) homePage = m.website || m.homepage || ''
    }
  }

  const containerClass = isMobile
    ? `fixed bottom-0 left-0 right-0 z-[1000] bg-white shadow-xl flex flex-col rounded-t-2xl transition-all duration-300 ${expanded ? 'h-[95vh]' : 'h-[58vh]'}`
    : 'fixed left-0 top-0 h-full w-96 bg-white shadow-xl z-[1000] flex flex-col'

  const dragHandle = isMobile && (
    <div className="flex justify-center pt-2 pb-1 cursor-pointer flex-shrink-0" onClick={() => setExpanded(e => !e)}>
      <div className="w-10 h-1 bg-gray-300 rounded-full" />
    </div>
  )

  if (loading) return (
    <div className={containerClass}>
      {dragHandle}
      <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">Loading…</div>
    </div>
  )

  if (error || !cafe) return (
    <div className={containerClass}>
      {dragHandle}
      <div className={`px-4 pb-3 flex items-center justify-between flex-shrink-0 ${isMobile ? 'pt-3' : 'pt-14'}`}>
        <span className="text-sm text-gray-400 font-mono truncate">{cafeId}</span>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl ml-2">✕</button>
      </div>
      <div className="flex-1 flex flex-col items-center justify-center gap-2 text-gray-400 text-sm">
        <span className="text-3xl">🔍</span>Not found in this snapshot.
      </div>
    </div>
  )

  const mapLinks = (cafe.providers ?? []).filter(p => PROVIDER_MAP_URL[p]).map(p => {
    const src = cafe.sources?.find(s => s.provider === p)
    return { provider: p, href: PROVIDER_MAP_URL[p](cafe.lat, cafe.lon, cafe.english_name || cafe.name, src) }
  })

  return (
    <>
      <div className={containerClass}>
        {dragHandle}

        {/* Header — desktop gets pt-14 to clear the logo pill sitting at top-left */}
        <div className={`px-4 pb-3 flex items-start gap-2 flex-shrink-0 ${isMobile ? 'pt-3' : 'pt-14'}`}>
          <div className="flex-1 min-w-0">
            <h2 className="font-bold text-lg truncate leading-tight">{cafe.english_name || cafe.name}</h2>
            {cafe.english_name && <p className="text-sm text-gray-400 truncate">{cafe.name}</p>}
            {cafe.chain_name && (
              <span className="inline-block mt-1 px-2 py-0.5 text-xs bg-amber-100 text-amber-700 rounded-full">
                {cafe.chain_name_english || cafe.chain_name}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1 flex-shrink-0 mt-0.5">
            <button
              onClick={() => navigate(`/cafe/${cafe.id}?source=all`)}
              className="text-gray-400 hover:text-blue-600 w-8 h-8 flex items-center justify-center rounded-lg hover:bg-blue-50 transition-colors"
              title="Full Details"
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4">
                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
                <polyline points="15,3 21,3 21,9"/>
                <line x1="10" y1="14" x2="21" y2="3"/>
              </svg>
            </button>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl w-8 h-8 flex items-center justify-center">✕</button>
          </div>
        </div>

        {/* Map links — color-coded by provider */}
        {mapLinks.length > 0 && (
          <div className="flex gap-2 px-3 pb-3 overflow-x-auto flex-shrink-0" style={{ scrollbarWidth: 'none' }}>
            {mapLinks.map(({ provider, href }) => (
              <a key={provider} href={href} target="_blank" rel="noopener noreferrer"
                className="flex-shrink-0 px-3 py-1.5 text-xs font-semibold rounded-full transition-opacity hover:opacity-90 whitespace-nowrap text-white"
                style={{ background: PROVIDER_COLORS[provider] ?? '#6b7280' }}>
                {PROVIDER_LABEL[provider] || provider} ↗
              </a>
            ))}
          </div>
        )}

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto overflow-x-hidden min-w-0">

          {/* Info section */}
          <div className="px-4 pb-4 space-y-2.5">
            {homePage && (
              <a href={homePage} target="_blank" rel="noopener noreferrer"
                className="flex items-center gap-2 px-3 py-2 bg-blue-50 rounded-xl text-sm text-blue-700 hover:bg-blue-100 transition-colors">
                <span>🌐</span>
                <span className="truncate flex-1">{homePage.replace(/^https?:\/\//, '').replace(/\/$/, '')}</span>
                <span className="text-blue-400 shrink-0 text-xs">↗</span>
              </a>
            )}
            {cafe.address && <p className="text-sm text-gray-500 px-1">{cafe.address}</p>}
            {bizhour && (
              <div className="flex items-start gap-2 text-sm text-gray-500 px-1">
                <span className="shrink-0 mt-0.5">🕒</span>
                <span className="whitespace-pre-wrap">{bizhour.replace(/\|/g, '\n')}</span>
              </div>
            )}
            {tel && (
              <div className="flex items-center gap-2 text-sm text-gray-500 px-1">
                <span>📞</span>
                <a href={`tel:${tel}`} className="hover:underline">{tel}</a>
              </div>
            )}
            {imageCountByProvider.length > 0 && (
              <div className="flex items-center gap-1.5 px-1 flex-wrap">
                <span className="text-xs text-gray-400">Images:</span>
                {imageCountByProvider.map(([provider, count]) => (
                  <span key={provider}
                    className="text-[11px] font-bold px-1.5 py-0.5 rounded text-white"
                    style={{ background: PROVIDER_COLORS[provider] ?? '#6b7280' }}>
                    {provider[0].toUpperCase()} {count}
                  </span>
                ))}
                <span className="text-xs font-semibold text-gray-500">{allImages.length}</span>
              </div>
            )}
          </div>

          {/* Gallery */}
          {allImages.length > 0 && (
            <div>
              {sortedTagCounts.length > 0 && (
                <div className="flex flex-wrap gap-1 px-3 pt-1 pb-2">
                  {sortedTagCounts.map(([tag, count]) => {
                    const isActive = activeTags?.has(tag)
                    const isStarred = starredTags?.has(tag)
                    return (
                      <button key={tag}
                        onClick={() => setTagFilter(tagFilter === tag ? null : tag)}
                        className={`px-1.5 py-0.5 text-[10px] rounded-full border transition-colors ${
                          tagFilter === tag ? 'bg-blue-600 text-white border-blue-600'
                          : isActive ? 'bg-blue-100 text-blue-700 border-blue-300'
                          : isStarred ? 'bg-amber-50 text-amber-700 border-amber-200'
                          : 'bg-gray-50 text-gray-600 border-gray-200 hover:border-blue-300'
                        }`}>
                        {isStarred && !isActive && <span className="text-amber-400">★</span>} {tag}{' '}
                        <span className={tagFilter === tag ? 'text-blue-200' : 'text-gray-400'}>{count}</span>
                      </button>
                    )
                  })}
                  {tagFilter && (
                    <button onClick={() => setTagFilter(null)} className="px-1.5 py-0.5 text-[10px] text-blue-500">✕ clear</button>
                  )}
                </div>
              )}
              <div className="grid grid-cols-2 gap-px bg-gray-100">
                {displayImages.map((img, i) => (
                  <ImageTile key={img.id} img={img} activeTags={activeTags} starredTags={starredTags} onClick={() => setLightboxIndex(i)} />
                ))}
              </div>
              <div className="px-3 py-2 text-xs text-gray-400 text-right">
                {tagFilter ? `${displayImages.length} of ${allImages.length} images` : `${allImages.length} images`}
              </div>
            </div>
          )}
        </div>
      </div>

      {lightboxIndex !== null && (
        <ImageLightbox
          images={displayImages}
          index={lightboxIndex}
          onClose={() => setLightboxIndex(null)}
          onPrev={() => setLightboxIndex(i => i !== null ? Math.max(0, i - 1) : null)}
          onNext={() => setLightboxIndex(i => i !== null ? Math.min(displayImages.length - 1, i + 1) : null)}
        />
      )}
    </>
  )
}

function ImageTile({ img, activeTags, starredTags, onClick }: {
  img: ImageInfo
  activeTags?: Set<string>
  starredTags?: Set<string>
  onClick: () => void
}) {
  const [error, setError] = useState(false)
  const src = img.local_path?.startsWith('../')
    ? img.local_path.replace('../data/seoul/', '/images/')
    : img.local_path || img.image_url

  if (error) return <div className="aspect-video bg-gray-50" />

  const hitTags = img.tags?.filter(t => activeTags?.has(t.tag) || starredTags?.has(t.tag)) ?? []

  const providerColor = PROVIDER_COLORS[img.provider] ?? '#6b7280'

  return (
    <div className="relative aspect-video cursor-pointer group overflow-hidden bg-gray-100" onClick={onClick}>
      <img src={src} alt="" loading="lazy"
        className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-200"
        onError={() => setError(true)} />
      {/* Provider color corner triangle — top-right */}
      <div className="absolute top-0 right-0 w-0 h-0" style={{
        borderStyle: 'solid',
        borderWidth: '0 10px 10px 0',
        borderColor: `transparent ${providerColor} transparent transparent`,
      }} />
      {hitTags.length > 0 && (
        <div className="absolute bottom-1 left-1 flex flex-wrap gap-0.5 max-w-[90%]">
          {hitTags.slice(0, 3).map(t => (
            <span key={t.tag}
              className={`text-[9px] px-1 py-0.5 rounded-full font-medium ${activeTags?.has(t.tag) ? 'bg-blue-600/90 text-white' : 'bg-amber-500/90 text-white'}`}>
              {t.tag}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function ImageLightbox({ images, index, onClose, onPrev, onNext }: {
  images: ImageInfo[]
  index: number
  onClose: () => void
  onPrev: () => void
  onNext: () => void
}) {
  const img = images[index]
  const [scale, setScale] = useState(1)
  const [translate, setTranslate] = useState({ x: 0, y: 0 })
  const dragging = useRef(false)
  const lastPos = useRef({ x: 0, y: 0 })
  const lastPinchDist = useRef<number | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => { setScale(1); setTranslate({ x: 0, y: 0 }) }, [index])

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowLeft') onPrev()
      else if (e.key === 'ArrowRight') onNext()
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose, onPrev, onNext])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const handler = (e: WheelEvent) => {
      e.preventDefault()
      setScale(s => { const n = Math.max(1, Math.min(5, s - e.deltaY * 0.004)); if (n <= 1) setTranslate({ x: 0, y: 0 }); return n })
    }
    el.addEventListener('wheel', handler, { passive: false })
    return () => el.removeEventListener('wheel', handler)
  }, [])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const handler = (e: TouchEvent) => { if (e.touches.length === 2) e.preventDefault() }
    el.addEventListener('touchmove', handler, { passive: false })
    return () => el.removeEventListener('touchmove', handler)
  }, [])

  const pinchDist = (touches: React.TouchList) =>
    Math.hypot(touches[1].clientX - touches[0].clientX, touches[1].clientY - touches[0].clientY)

  const src = img.local_path?.startsWith('../')
    ? img.local_path.replace('../data/seoul/', '/images/')
    : img.local_path || img.image_url

  return (
    <div className="fixed inset-0 z-[2000] bg-black flex flex-col">
      <div className="shrink-0 flex items-start justify-between gap-2 px-4 py-3 bg-gradient-to-b from-black to-transparent pointer-events-none">
        <div className="flex flex-wrap gap-1 flex-1 min-w-0 pointer-events-auto">
          {img.tags?.length ? img.tags.map(t => (
            <span key={t.tag} className="text-xs bg-white/20 backdrop-blur-sm text-white px-2 py-0.5 rounded-full">
              {t.tag} <span className="text-white/50 text-[10px]">{t.score.toFixed(2)}</span>
            </span>
          )) : <span className="text-xs text-white/30">no tags</span>}
        </div>
        <div className="flex items-center gap-3 shrink-0 pointer-events-auto">
          <span className="text-white/40 text-xs font-mono">{index + 1}/{images.length}</span>
          <button onClick={onClose} className="text-white/70 hover:text-white text-xl">✕</button>
        </div>
      </div>

      <div
        ref={containerRef}
        className="flex-1 flex items-center justify-center overflow-hidden relative"
        style={{ cursor: scale > 1 ? 'grab' : 'default', touchAction: 'none' }}
        onMouseDown={e => { if (scale <= 1) return; dragging.current = true; lastPos.current = { x: e.clientX, y: e.clientY } }}
        onMouseMove={e => {
          if (!dragging.current) return
          const dx = e.clientX - lastPos.current.x; const dy = e.clientY - lastPos.current.y
          lastPos.current = { x: e.clientX, y: e.clientY }
          setTranslate(t => ({ x: t.x + dx, y: t.y + dy }))
        }}
        onMouseUp={() => { dragging.current = false }}
        onMouseLeave={() => { dragging.current = false }}
        onTouchStart={e => {
          if (e.touches.length === 2) lastPinchDist.current = pinchDist(e.touches)
          else if (e.touches.length === 1 && scale > 1) { dragging.current = true; lastPos.current = { x: e.touches[0].clientX, y: e.touches[0].clientY } }
        }}
        onTouchMove={e => {
          if (e.touches.length === 2 && lastPinchDist.current !== null) {
            const d = pinchDist(e.touches); const ratio = d / lastPinchDist.current; lastPinchDist.current = d
            setScale(s => { const n = Math.max(1, Math.min(5, s * ratio)); if (n <= 1) setTranslate({ x: 0, y: 0 }); return n })
          } else if (e.touches.length === 1 && dragging.current) {
            const dx = e.touches[0].clientX - lastPos.current.x; const dy = e.touches[0].clientY - lastPos.current.y
            lastPos.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }
            setTranslate(t => ({ x: t.x + dx, y: t.y + dy }))
          }
        }}
        onTouchEnd={() => { dragging.current = false; lastPinchDist.current = null }}
      >
        <img src={src} alt="" draggable={false}
          className="max-w-full max-h-full object-contain select-none"
          style={{ transform: `translate(${translate.x}px, ${translate.y}px) scale(${scale})`, transformOrigin: 'center' }}
        />
        {index > 0 && (
          <button
            className="absolute left-2 top-1/2 -translate-y-1/2 text-white/70 hover:text-white text-3xl px-3 py-4 bg-black/30 hover:bg-black/60 rounded-full transition-colors z-10"
            onClick={e => { e.stopPropagation(); onPrev() }}>‹</button>
        )}
        {index < images.length - 1 && (
          <button
            className="absolute right-2 top-1/2 -translate-y-1/2 text-white/70 hover:text-white text-3xl px-3 py-4 bg-black/30 hover:bg-black/60 rounded-full transition-colors z-10"
            onClick={e => { e.stopPropagation(); onNext() }}>›</button>
        )}
      </div>

      <div className="shrink-0 py-2 text-center">
        <span className="text-xs text-white/30 font-mono">{img.provider}</span>
      </div>
    </div>
  )
}
