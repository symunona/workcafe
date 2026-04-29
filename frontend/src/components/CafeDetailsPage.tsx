import { useEffect, useState, useCallback, useMemo } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import type { CleanCafe } from '../types'
import { PROVIDER_COLORS } from '../utils'
import { useSnapshot } from './SnapshotSelector'
import { TaggedImage } from './TaggedImage'

export function CafeDetailsPage({ activeTags }: { activeTags?: Set<string> }) {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [cafe, setCafe] = useState<CleanCafe | null>(null)
  const [loading, setLoading] = useState(true)
  const { snapshot, apiUrl } = useSnapshot()

  const activeTab = searchParams.get('source') || 'all'
  const imageIndexParam = searchParams.get('image')
  const [activeImageIndex, setActiveImageIndex] = useState<number | null>(
    imageIndexParam !== null ? parseInt(imageIndexParam, 10) : null
  )

  useEffect(() => {
    setLoading(true)
    fetch(apiUrl(`/api/clean_cafe?id=${encodeURIComponent(id || '')}`))
      .then(r => r.json())
      .then(data => { setCafe(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [id, snapshot]) // eslint-disable-line react-hooks/exhaustive-deps

  const [activeTagFilter, setActiveTagFilter] = useState<Set<string>>(() => new Set(activeTags))
  const [hoveredTag, setHoveredTag] = useState<string | null>(null)

  const sources = cafe?.sources || []
  const allImages = cafe?.all_images || []
  const currentSource = activeTab === 'all' ? null : sources.find(s => s.id === activeTab)
  const displayImages = currentSource ? currentSource.images || [] : allImages

  const tagCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const img of displayImages) {
      for (const t of img.tags ?? []) counts.set(t.tag, (counts.get(t.tag) ?? 0) + 1)
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [displayImages])

  const sortedDisplayImages = useMemo(() => {
    if (displayImages.every(img => !img.tags?.length)) return displayImages
    if (activeTagFilter.size) {
      return [...displayImages].sort((a, b) => {
        const sa = Math.max(0, ...(a.tags?.filter(t => activeTagFilter.has(t.tag)).map(t => t.score) ?? []))
        const sb = Math.max(0, ...(b.tags?.filter(t => activeTagFilter.has(t.tag)).map(t => t.score) ?? []))
        return sb - sa
      })
    }
    return [...displayImages].sort((a, b) => (b.tags?.length ?? 0) - (a.tags?.length ?? 0))
  }, [displayImages, activeTagFilter])

  const filteredImages = activeTagFilter.size === 0
    ? sortedDisplayImages
    : sortedDisplayImages.filter(img => img.tags?.some(t => activeTagFilter.has(t.tag)))

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (activeImageIndex === null) return
    if (e.key === 'ArrowRight') setActiveImageIndex(prev => prev !== null && prev < displayImages.length - 1 ? prev + 1 : prev)
    else if (e.key === 'ArrowLeft') setActiveImageIndex(prev => prev !== null && prev > 0 ? prev - 1 : prev)
    else if (e.key === 'Escape') setActiveImageIndex(null)
  }, [activeImageIndex, displayImages.length])

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])

  // Extract info
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

  if (loading) return <div className="fixed inset-0 bg-white z-[1100] flex items-center justify-center text-gray-400">Loading...</div>
  if (!cafe) return <div className="fixed inset-0 bg-white z-[1100] flex items-center justify-center text-red-400">Cafe not found</div>

  const activeImage = activeImageIndex !== null ? displayImages[activeImageIndex] : null

  return (
    <div className="fixed inset-0 bg-white z-[1100] flex flex-col animate-in slide-in-from-bottom-4">
      {/* Header */}
      <header className="px-4 py-3 flex items-center gap-3 bg-white shadow-sm shrink-0 z-20">
        <button
          onClick={() => navigate(`/cafe/${cafe.id}`)}
          className="text-gray-500 hover:text-gray-800 text-sm px-2 py-1 rounded-lg hover:bg-gray-100 transition-colors flex-shrink-0 flex items-center gap-1"
        >
          ← Back
        </button>
        <div className="flex-1 min-w-0">
          <h1 className="text-base font-bold truncate">{cafe.english_name || cafe.name}</h1>
          {cafe.english_name && <p className="text-xs text-gray-400 truncate">{cafe.name}</p>}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {cafe.providers.map(p => (
            <span key={p} className="px-2 py-0.5 text-xs text-white rounded-full font-medium"
              style={{ background: PROVIDER_COLORS[p] ?? '#6b7280' }}>
              {p}
            </span>
          ))}
        </div>
      </header>

      {/* Source tabs */}
      <div className="flex overflow-x-auto shrink-0 bg-white" style={{ scrollbarWidth: 'none', boxShadow: '0 1px 0 rgba(0,0,0,0.08)' }}>
        <button
          className={`px-4 py-2.5 text-sm font-medium whitespace-nowrap transition-colors ${activeTab === 'all' ? 'border-b-2 border-blue-600 text-blue-600' : 'text-gray-500 hover:text-gray-700'}`}
          onClick={() => { setSearchParams({ source: 'all' }); setActiveImageIndex(null); setActiveTagFilter(new Set()) }}
        >
          All ({allImages.length})
        </button>
        {sources.map(src => (
          <button key={src.id}
            className={`px-4 py-2.5 text-sm font-medium whitespace-nowrap flex items-center gap-1.5 transition-colors ${activeTab === src.id ? 'border-b-2 border-blue-600 text-blue-600' : 'text-gray-500 hover:text-gray-700'}`}
            onClick={() => { setSearchParams({ source: src.id }); setActiveImageIndex(null); setActiveTagFilter(new Set()) }}
          >
            <span className="w-2 h-2 rounded-full" style={{ background: PROVIDER_COLORS[src.provider] ?? '#6b7280' }} />
            {src.provider}
            <span className="text-xs opacity-60">({src.images?.length ?? 0})</span>
          </button>
        ))}
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto bg-gray-50">

        {/* Tag filter chips */}
        {tagCounts.length > 0 && (
          <div className="px-4 pt-3 pb-2 flex flex-wrap gap-1.5 bg-white">
            {activeTagFilter.size > 0 && (
              <button className="px-2 py-0.5 text-xs text-gray-400 hover:text-gray-600" onClick={() => setActiveTagFilter(new Set())}>
                ✕ clear
              </button>
            )}
            {tagCounts.map(([tag, count]) => {
              const active = activeTagFilter.has(tag)
              return (
                <button key={tag}
                  onClick={() => setActiveTagFilter(prev => {
                    const next = new Set(prev)
                    if (active) next.delete(tag); else next.add(tag)
                    return next
                  })}
                  className={`px-2 py-0.5 text-xs rounded-full border transition-colors ${active ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-600 border-gray-200 hover:border-blue-400'}`}>
                  {tag} <span className={active ? 'text-blue-200' : 'text-gray-400'}>{count}</span>
                </button>
              )
            })}
          </div>
        )}

        {/* Gallery */}
        <div className="p-3">
          {filteredImages.length === 0 ? (
            <div className="text-gray-400 text-center py-12 text-sm">
              {displayImages.length === 0 ? 'No images available' : 'No images match filter'}
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
              {filteredImages.map((img, i) => (
                <div key={i}
                  className="cursor-pointer rounded-xl overflow-hidden shadow-sm hover:shadow-md transition-shadow group bg-white"
                  onClick={() => setActiveImageIndex(displayImages.indexOf(img))}>
                  <div className="relative aspect-video overflow-hidden">
                    <img
                      src={img.local_path?.startsWith('../') ? img.local_path.replace('../data/seoul/', '/images/') : img.local_path || img.image_url}
                      className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                      alt="" loading="lazy"
                    />
                    <span className="absolute bottom-1.5 right-1.5 text-[10px] font-medium px-1.5 py-0.5 rounded-md text-white"
                      style={{ background: PROVIDER_COLORS[img.provider] ?? '#6b7280' }}>
                      {img.provider}
                    </span>
                  </div>
                  {img.tags?.length > 0 && (
                    <div className="px-2 py-1.5 flex flex-wrap gap-1">
                      {img.tags.map(t => (
                        <span key={t.tag} className="text-[10px] bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded-full">{t.tag}</span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Info section below gallery */}
        <div className="bg-white mx-3 mb-3 rounded-xl shadow-sm px-4 py-4 space-y-3">
          <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Info & Details</div>
          {cafe.address && <p className="text-sm text-gray-600">{cafe.address}</p>}
          {cafe.chain_name && (
            <div className="text-sm text-gray-500">
              Chain: <span className="text-gray-700 font-medium">{cafe.chain_name_english || cafe.chain_name}</span>
            </div>
          )}
          {homePage && (
            <a href={homePage} target="_blank" rel="noopener noreferrer"
              className="flex items-center gap-2 text-sm text-blue-600 hover:underline">
              <span>🌐</span>{homePage.replace(/^https?:\/\//, '').replace(/\/$/, '')} ↗
            </a>
          )}
          {bizhour && (
            <div className="flex items-start gap-2 text-sm text-gray-600">
              <span className="shrink-0">🕒</span>
              <span className="whitespace-pre-wrap">{bizhour.replace(/\|/g, '\n')}</span>
            </div>
          )}
          {tel && (
            <div className="flex items-center gap-2 text-sm text-gray-600">
              <span>📞</span><a href={`tel:${tel}`} className="hover:underline">{tel}</a>
            </div>
          )}
          <div className="pt-1 space-y-2">
            <div className="text-xs font-medium text-gray-400 uppercase tracking-wider">Sources</div>
            {sources.map(src => (
              <div key={src.id} className="flex items-center justify-between text-sm">
                <span className="flex items-center gap-2 min-w-0">
                  <span className="px-1.5 py-0.5 text-white rounded text-[10px] font-medium shrink-0"
                    style={{ background: PROVIDER_COLORS[src.provider] ?? '#6b7280' }}>
                    {src.provider}
                  </span>
                  <span className="text-gray-600 truncate">{src.name}</span>
                </span>
                {src.url && (
                  <a href={src.url} target="_blank" rel="noopener noreferrer"
                    className="text-blue-500 text-xs hover:underline shrink-0 ml-2">↗</a>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Fullscreen image lightbox */}
      {activeImage && (
        <div className="absolute inset-0 bg-black/95 z-50 flex flex-col">
          <div className="flex-1 relative flex items-center justify-center p-4">
            {activeImageIndex !== null && activeImageIndex > 0 && (
              <button className="absolute left-4 text-white/50 hover:text-white text-4xl z-10"
                onClick={e => { e.stopPropagation(); setActiveImageIndex(activeImageIndex - 1) }}>‹</button>
            )}
            <TaggedImage
              src={activeImage.local_path?.startsWith('../') ? activeImage.local_path.replace('../data/seoul/', '/images/') : activeImage.local_path || activeImage.image_url}
              tags={activeImage.tags ?? []}
              hoveredTag={hoveredTag}
              imgClassName="max-w-full max-h-[80vh] rounded shadow-2xl"
            />
            {activeImageIndex !== null && activeImageIndex < displayImages.length - 1 && (
              <button className="absolute right-4 text-white/50 hover:text-white text-4xl z-10"
                onClick={e => { e.stopPropagation(); setActiveImageIndex(activeImageIndex + 1) }}>›</button>
            )}
          </div>
          <div className="shrink-0 pb-4 flex flex-col items-center gap-2">
            <div className="flex items-center gap-3">
              <span className="text-white/60 text-sm">{activeImageIndex !== null ? activeImageIndex + 1 : 0} / {displayImages.length}</span>
              <span className="px-2 py-0.5 text-xs text-white rounded-full font-medium"
                style={{ background: PROVIDER_COLORS[activeImage.provider] ?? '#6b7280' }}>
                {activeImage.provider}
              </span>
            </div>
            {activeImage.tags?.length > 0 && (
              <div className="flex flex-wrap gap-1.5 justify-center px-4">
                {activeImage.tags.map(t => (
                  <span key={t.tag}
                    className={`px-2 py-0.5 text-xs rounded-full cursor-default transition-colors ${hoveredTag === t.tag ? 'bg-blue-500 text-white' : 'bg-white/20 text-white'}`}
                    onMouseEnter={() => setHoveredTag(t.tag)}
                    onMouseLeave={() => setHoveredTag(null)}>
                    {t.tag}
                  </span>
                ))}
              </div>
            )}
          </div>
          <button className="absolute top-4 right-4 text-white/70 hover:text-white bg-black/50 rounded-full w-10 h-10 flex items-center justify-center text-xl"
            onClick={() => { setActiveImageIndex(null); setHoveredTag(null) }}>✕</button>
        </div>
      )}
    </div>
  )
}
