import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import type { CleanCafe } from '../types'
import { PROVIDER_COLORS } from '../utils'

export function CafeDetailsPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [cafe, setCafe] = useState<CleanCafe | null>(null)
  const [loading, setLoading] = useState(true)
  
  const activeTab = searchParams.get('source') || 'all'
  const imageIndexParam = searchParams.get('image')
  const [activeImageIndex, setActiveImageIndex] = useState<number | null>(
    imageIndexParam !== null ? parseInt(imageIndexParam, 10) : null
  )

  useEffect(() => {
    setLoading(true)
    fetch(`/api/clean_cafe?id=${encodeURIComponent(id || '')}`)
      .then(r => r.json())
      .then(data => { setCafe(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [id])

  const sources = cafe?.sources || []
  const allImages = cafe?.all_images || []
  const currentSource = activeTab === 'all' ? null : sources.find(s => s.id === activeTab)
  const displayImages = currentSource ? currentSource.images || [] : allImages

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

  if (loading) return <div className="fixed top-0 left-96 right-0 bottom-0 bg-white/95 backdrop-blur-sm z-[1000] p-8 text-gray-500 border-l">Loading...</div>
  if (!cafe) return <div className="fixed top-0 left-96 right-0 bottom-0 bg-white/95 backdrop-blur-sm z-[1000] p-8 text-red-500 border-l">Cafe not found</div>

  const activeImage = activeImageIndex !== null ? displayImages[activeImageIndex] : null

  return (
    <div className="fixed top-0 left-96 right-0 bottom-0 bg-white/95 backdrop-blur-sm z-[1000] flex flex-col shadow-2xl border-l animate-in slide-in-from-right-8">
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
          <h2 className="text-lg font-semibold mb-4">Gallery ({displayImages.length})</h2>
          
          {activeImage ? (
            <div className="absolute inset-0 bg-black/95 z-50 flex flex-col items-center justify-center p-4">
              <div className="relative w-full h-full flex items-center justify-center">
                {activeImageIndex !== null && activeImageIndex > 0 && (
                  <button className="absolute left-4 text-white/50 hover:text-white text-4xl z-10"
                    onClick={(e) => { e.stopPropagation(); setActiveImageIndex(activeImageIndex - 1) }}>‹</button>
                )}
                
                <img
                  src={activeImage.local_path?.startsWith('../') ? activeImage.local_path.replace('../data/seoul/', '/images/') : activeImage.local_path || activeImage.image_url}
                  alt=""
                  className="max-w-full max-h-[85vh] object-contain rounded shadow-2xl"
                />
                
                {activeImageIndex !== null && activeImageIndex < displayImages.length - 1 && (
                  <button className="absolute right-4 text-white/50 hover:text-white text-4xl z-10"
                    onClick={(e) => { e.stopPropagation(); setActiveImageIndex(activeImageIndex + 1) }}>›</button>
                )}
              </div>
              <div className="mt-4 flex items-center gap-4">
                <span className="text-white/70 text-sm">{activeImageIndex !== null ? activeImageIndex + 1 : 0} / {displayImages.length}</span>
                <span className="px-3 py-1 text-sm text-white rounded-full font-medium shadow"
                  style={{ background: PROVIDER_COLORS[activeImage.provider] ?? '#6b7280' }}>
                  {activeImage.provider}
                </span>
              </div>
              <button className="absolute top-4 right-4 text-white/70 hover:text-white bg-black/50 hover:bg-black/80 rounded-full w-10 h-10 flex items-center justify-center transition-colors text-xl"
                onClick={() => setActiveImageIndex(null)}>✕</button>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {displayImages.map((img, i) => (
                <div key={i} className="relative aspect-video cursor-pointer rounded-xl overflow-hidden shadow-sm hover:shadow-xl transition-all duration-300 group bg-white"
                  style={{ borderBottom: `4px solid ${PROVIDER_COLORS[img.provider] ?? '#6b7280'}` }}
                  onClick={() => setActiveImageIndex(i)}>
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
              ))}
            </div>
          )}
          {!activeImage && displayImages.length === 0 && (
            <div className="text-gray-400 text-center py-12">No images available</div>
          )}
        </div>

        {/* Right Sidebar (Tabs & Metadata) */}
        <div className="w-96 border-l bg-white flex flex-col z-10">
          <div className="flex overflow-x-auto shadow-sm bg-white z-10">
            <button
              className={`px-4 py-3 text-sm font-medium whitespace-nowrap ${activeTab === 'all' ? 'border-b-2 border-blue-600 text-blue-600' : 'text-gray-500 hover:text-gray-700'}`}
              onClick={() => { setSearchParams({ source: 'all' }); setActiveImageIndex(null) }}
            >
              All Data
            </button>
            {sources.map(src => (
              <button
                key={src.id}
                className={`px-4 py-3 text-sm font-medium whitespace-nowrap flex items-center gap-1.5 ${activeTab === src.id ? 'border-b-2 border-blue-600 text-blue-600' : 'text-gray-500 hover:text-gray-700'}`}
                onClick={() => { setSearchParams({ source: src.id }); setActiveImageIndex(null) }}
              >
                <span className="w-2 h-2 rounded-full" style={{ background: PROVIDER_COLORS[src.provider] ?? '#6b7280' }} />
                {src.provider}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-y-auto p-4">
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
