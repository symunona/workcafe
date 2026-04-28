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
      for (const t of img.tags ?? []) {
        counts.set(t.tag, (counts.get(t.tag) ?? 0) + 1)
      }
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
    if (e.key === 'ArrowRight') {
      setActiveImageIndex(prev => (prev !== null && prev < displayImages.length - 1 ? prev + 1 : prev))
    } else if (e.key === 'ArrowLeft') {
      setActiveImageIndex(prev => (prev !== null && prev > 0 ? prev - 1 : prev))
    } else if (e.key === 'Escape') {
      setActiveImageIndex(null)
    }
  }, [activeImageIndex, displayImages.length])

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])

  if (loading) return <div className="fixed inset-0 bg-white z-[1100] p-8 text-gray-500">Loading...</div>
  if (!cafe) return <div className="fixed inset-0 bg-white z-[1100] p-8 text-red-500">Cafe not found</div>

  const activeImage = activeImageIndex !== null ? displayImages[activeImageIndex] : null

  return (
    <div className="fixed inset-0 bg-white z-[1100] flex flex-col shadow-2xl animate-in slide-in-from-bottom-4">
      {/* Header */}
      <header className="p-4 shadow-sm flex items-center gap-4 bg-white sticky top-0 z-20">
        <button onClick={() => navigate(`/cafe/${cafe.id}`)} className="text-gray-500 hover:text-black">✕ Close</button>
        <div>
          <h1 className="text-xl font-bold">{cafe.english_name || cafe.name}</h1>
          {cafe.english_name && <p className="text-sm text-gray-500">{cafe.name}</p>}
        </div>
        <div className="ml-auto flex gap-2">
          {cafe.providers.map(p => (
            <span key={p} className="px-2 py-1 text-xs text-white rounded-full font-medium"
              style={{ background: PROVIDER_COLORS[p] ?? '#6b7280' }}>
              {p}
            </span>
          ))}
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Main Content (Gallery) */}
        <div className="flex-1 p-6 overflow-y-auto bg-gray-100 relative">
          <div className="flex items-baseline gap-3 mb-4">
            <h2 className="text-lg font-semibold">Gallery ({activeTagFilter.size > 0 ? `${filteredImages.length}/` : ''}{displayImages.length})</h2>
            {activeTagFilter.size > 0 && (
              <button className="text-xs text-gray-400 hover:text-gray-600" onClick={() => setActiveTagFilter(new Set())}>clear filter</button>
            )}
          </div>

          {tagCounts.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-4">
              {tagCounts.map(([tag, count]) => {
                const active = activeTagFilter.has(tag)
                return (
                  <button key={tag}
                    onClick={() => setActiveTagFilter(prev => {
                      const next = new Set(prev)
                      if (active) next.delete(tag); else next.add(tag)
                      return next
                    })}
                    className={`px-2 py-0.5 text-xs rounded-full border transition-colors ${active ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'}`}>
                    {tag} <span className={active ? 'text-blue-200' : 'text-gray-400'}>{count}</span>
                  </button>
                )
              })}
            </div>
          )}

          {activeImage ? (
            <div className="absolute inset-0 bg-black/95 z-50 flex flex-col items-center justify-center p-4">
              <div className="relative w-full h-full flex items-center justify-center">
                {activeImageIndex !== null && activeImageIndex > 0 && (
                  <button className="absolute left-4 text-white/50 hover:text-white text-4xl z-10"
                    onClick={(e) => { e.stopPropagation(); setActiveImageIndex(activeImageIndex - 1) }}>‹</button>
                )}

                <TaggedImage
                  src={activeImage.local_path?.startsWith('../') ? activeImage.local_path.replace('../data/seoul/', '/images/') : activeImage.local_path || activeImage.image_url}
                  tags={activeImage.tags ?? []}
                  hoveredTag={hoveredTag}
                  imgClassName="max-w-full max-h-[85vh] rounded shadow-2xl"
                />

                {activeImageIndex !== null && activeImageIndex < displayImages.length - 1 && (
                  <button className="absolute right-4 text-white/50 hover:text-white text-4xl z-10"
                    onClick={(e) => { e.stopPropagation(); setActiveImageIndex(activeImageIndex + 1) }}>›</button>
                )}
              </div>
              <div className="mt-4 flex flex-col items-center gap-2">
                <div className="flex items-center gap-4">
                  <span className="text-white/70 text-sm">{activeImageIndex !== null ? activeImageIndex + 1 : 0} / {displayImages.length}</span>
                  <span className="px-3 py-1 text-sm text-white rounded-full font-medium shadow"
                    style={{ background: PROVIDER_COLORS[activeImage.provider] ?? '#6b7280' }}>
                    {activeImage.provider}
                  </span>
                </div>
                {activeImage.tags?.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 justify-center">
                    {activeImage.tags.map(t => (
                      <span key={t.tag}
                        className={`px-2 py-0.5 text-xs rounded-full backdrop-blur-sm cursor-default transition-colors ${hoveredTag === t.tag ? 'bg-blue-500 text-white' : 'bg-white/20 text-white'}`}
                        onMouseEnter={() => setHoveredTag(t.tag)}
                        onMouseLeave={() => setHoveredTag(null)}
                      >
                        {t.tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <button className="absolute top-4 right-4 text-white/70 hover:text-white bg-black/50 hover:bg-black/80 rounded-full w-10 h-10 flex items-center justify-center transition-colors text-xl"
                onClick={() => { setActiveImageIndex(null); setHoveredTag(null) }}>✕</button>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {filteredImages.map((img, i) => (
                <div key={i} className="cursor-pointer rounded-xl shadow-sm hover:shadow-xl transition-all duration-300 group bg-white flex flex-col overflow-hidden"
                  style={{ borderBottom: `4px solid ${PROVIDER_COLORS[img.provider] ?? '#6b7280'}` }}
                  onClick={() => setActiveImageIndex(displayImages.indexOf(img))}>
                  <div className="relative aspect-video overflow-hidden">
                    <img
                      src={img.local_path?.startsWith('../') ? img.local_path.replace('../data/seoul/', '/images/') : img.local_path || img.image_url}
                      className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
                      alt=""
                      loading="lazy"
                    />
                    <span className="absolute bottom-2 right-2 text-xs bg-black/60 backdrop-blur-sm text-white px-2 py-1 rounded-md font-medium">
                      {img.provider}
                    </span>
                  </div>
                  {img.tags?.length > 0 && (
                    <div className="px-2 py-1.5 flex flex-wrap gap-1">
                      {img.tags.map(t => (
                        <span key={t.tag} className="text-[10px] bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded-full">
                          {t.tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          {!activeImage && filteredImages.length === 0 && (
            <div className="text-gray-400 text-center py-12">{displayImages.length === 0 ? 'No images available' : 'No images match filter'}</div>
          )}
        </div>

        {/* Right Sidebar (Tabs & Metadata) */}
        <div className="w-96 border-l bg-white flex flex-col z-10">
          <div className="flex overflow-x-auto shadow-sm bg-white z-10">
            <button
              className={`px-4 py-3 text-sm font-medium whitespace-nowrap ${activeTab === 'all' ? 'border-b-2 border-blue-600 text-blue-600' : 'text-gray-500 hover:text-gray-700'}`}
              onClick={() => { setSearchParams({ source: 'all' }); setActiveImageIndex(null); setActiveTagFilter(new Set()) }}
            >
              All Data
            </button>
            {sources.map(src => (
              <button
                key={src.id}
                className={`px-4 py-3 text-sm font-medium whitespace-nowrap flex items-center gap-1.5 ${activeTab === src.id ? 'border-b-2 border-blue-600 text-blue-600' : 'text-gray-500 hover:text-gray-700'}`}
                onClick={() => { setSearchParams({ source: src.id }); setActiveImageIndex(null); setActiveTagFilter(new Set()) }}
              >
                <span className="w-2 h-2 rounded-full" style={{ background: PROVIDER_COLORS[src.provider] ?? '#6b7280' }} />
                {src.provider}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-y-auto p-4">
            {/* Image tags — shown when an image is selected */}
            {activeImage && activeImage.tags?.length > 0 && (
              <div className="mb-4 pb-4 border-b">
                <h3 className="text-sm font-bold text-gray-500 uppercase tracking-wider mb-2">Image Tags</h3>
                <div className="flex flex-wrap gap-1.5">
                  {activeImage.tags.map(t => (
                    <span key={t.tag}
                      className={`px-2 py-0.5 text-xs border rounded-full cursor-default transition-colors ${hoveredTag === t.tag ? 'bg-blue-600 text-white border-blue-600' : 'bg-blue-50 text-blue-700 border-blue-200'} ${t.boxes?.length ? 'cursor-crosshair' : ''}`}
                      title={t.boxes?.length ? `${t.boxes.length} detection(s) — hover to highlight` : undefined}
                      onMouseEnter={() => t.boxes?.length && setHoveredTag(t.tag)}
                      onMouseLeave={() => setHoveredTag(null)}
                    >
                      {t.tag}{t.boxes?.length ? ` ×${t.boxes.length}` : ''}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {activeTab === 'all' ? (
              <div className="space-y-4">
                <div>
                  <h3 className="text-sm font-bold text-gray-500 uppercase tracking-wider mb-1">ADDRESS</h3>
                  <p className="text-sm">{cafe.address || 'N/A'}</p>
                </div>
                {cafe.chain_name && (
                  <div>
                    <h3 className="text-sm font-bold text-gray-500 uppercase tracking-wider mb-1">CHAIN</h3>
                    <p className="text-sm">{cafe.chain_name_english || cafe.chain_name}</p>
                  </div>
                )}
                <div>
                  <h3 className="text-sm font-bold text-gray-500 uppercase tracking-wider mb-1">SOURCES</h3>
                  <div className="space-y-2 mt-2">
                    {sources.map(src => (
                      <div key={src.id} className="text-sm flex items-center justify-between">
                        <span className="flex items-center gap-2">
                          <span className="w-2 h-2 rounded-full" style={{ background: PROVIDER_COLORS[src.provider] ?? '#6b7280' }} />
                          {src.name}
                        </span>
                        <span className="text-gray-400 text-xs">{src.images?.length || 0} imgs</span>
                      </div>
                    ))}
                  </div>
                </div>
                
                {/* External links */}
                <div>
                  <h3 className="text-sm font-bold text-gray-500 uppercase tracking-wider mb-1 mt-4">EXTERNAL LINKS</h3>
                  <div className="space-y-2 mt-2">
                    {sources.filter(s => s.url).map(src => (
                      <a key={src.id} href={src.url} target="_blank" rel="noopener noreferrer" 
                         className="text-sm flex items-center gap-2 text-blue-500 hover:underline">
                        <span className="w-2 h-2 rounded-full" style={{ background: PROVIDER_COLORS[src.provider] ?? '#6b7280' }} />
                        Open in {src.provider} ↗
                      </a>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                {currentSource && (
                  <>
                    <div>
                      <h3 className="text-sm font-bold text-gray-500 uppercase tracking-wider mb-1">NAME</h3>
                      <p className="text-sm">{currentSource.name}</p>
                    </div>
                    <div>
                      <h3 className="text-sm font-bold text-gray-500 uppercase tracking-wider mb-1">ADDRESS</h3>
                      <p className="text-sm">{currentSource.address || 'N/A'}</p>
                    </div>
                    {currentSource.url && (
                      <div>
                        <h3 className="text-sm font-bold text-gray-500 uppercase tracking-wider mb-1">URL</h3>
                        <a href={currentSource.url} target="_blank" rel="noopener noreferrer" className="text-sm text-blue-500 hover:underline break-all">
                          {currentSource.url}
                        </a>
                      </div>
                    )}
                    <div>
                      <h3 className="text-sm font-bold text-gray-500 uppercase tracking-wider mb-1">RAW METADATA</h3>
                      <pre className="text-xs bg-gray-50 p-2 rounded overflow-x-auto border">
                        {JSON.stringify(currentSource.metadata, null, 2)}
                      </pre>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
