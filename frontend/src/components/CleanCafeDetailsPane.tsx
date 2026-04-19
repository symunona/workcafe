import { useEffect, useState } from 'react'
import type { CleanCafe, ImageInfo } from '../types'
import { PROVIDER_COLORS } from '../utils'

interface Props {
  cafeId: string
  onClose: () => void
}

export function CleanCafeDetailsPane({ cafeId, onClose }: Props) {
  const [cafe, setCafe] = useState<CleanCafe | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeImage, setActiveImage] = useState<ImageInfo | null>(null)

  useEffect(() => {
    setLoading(true)
    setCafe(null)
    fetch(`/api/clean_cafe?id=${encodeURIComponent(cafeId)}`)
      .then(r => r.json())
      .then(data => { setCafe(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [cafeId])

  if (loading) return (
    <div className="fixed right-0 top-0 h-full w-96 bg-white shadow-xl z-[1000] flex items-center justify-center">
      <div className="text-gray-500">Loading...</div>
    </div>
  )

  if (!cafe) return null

  const allImages = cafe.all_images ?? []
  const providers = cafe.providers ?? []

  return (
    <div className="fixed right-0 top-0 h-full w-96 bg-white shadow-xl z-[1000] flex flex-col overflow-hidden">
      {/* Header */}
      <div className="p-4 border-b bg-gray-50 flex items-start gap-2">
        <div className="flex-1 min-w-0">
          <h2 className="font-bold text-lg truncate">{cafe.name}</h2>
          {cafe.english_name && <p className="text-sm text-gray-500 truncate">{cafe.english_name}</p>}
          {cafe.chain_name && (
            <span className="inline-block mt-1 px-2 py-0.5 text-xs bg-amber-100 text-amber-700 rounded-full">
              {cafe.chain_name_english || cafe.chain_name}
            </span>
          )}
        </div>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl flex-shrink-0">✕</button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Provider badges */}
        <div className="px-4 pt-3 flex gap-2 flex-wrap">
          {providers.map(p => (
            <span key={p} className="px-2 py-0.5 text-xs text-white rounded-full font-medium"
              style={{ background: PROVIDER_COLORS[p] ?? '#6b7280' }}>
              {p}
            </span>
          ))}
          <span className="text-xs text-gray-400 self-center">{allImages.length} images</span>
        </div>

        {/* Address */}
        {cafe.address && (
          <p className="px-4 pt-2 text-sm text-gray-600">{cafe.address}</p>
        )}

        {/* Image gallery */}
        {allImages.length > 0 && (
          <div className="px-4 pt-3">
            <p className="text-xs text-gray-500 mb-2 font-medium">Gallery</p>
            <div className="grid grid-cols-3 gap-1">
              {allImages.slice(0, 30).map((img, i) => (
                <div key={i} className="relative aspect-square cursor-pointer"
                  style={{ borderBottom: `3px solid ${PROVIDER_COLORS[img.provider] ?? '#6b7280'}` }}
                  onClick={() => setActiveImage(img)}>
                  <img
                    src={img.local_path?.startsWith('../') ? img.local_path.replace('../data/seoul/', '/images/') : img.local_path || img.image_url}
                    className="w-full h-full object-cover"
                    alt=""
                    loading="lazy"
                  />
                  <span className="absolute bottom-0.5 right-0.5 text-[9px] bg-black/50 text-white px-0.5 rounded">
                    {img.provider}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Sources (scrapers that found this cafe) */}
        {cafe.sources && cafe.sources.length > 0 && (
          <div className="px-4 pt-4">
            <p className="text-xs text-gray-500 mb-2 font-medium">Sources ({cafe.sources.length})</p>
            <div className="space-y-2">
              {cafe.sources.map(src => (
                <div key={src.id} className="border rounded p-2 text-xs">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="px-1.5 py-0.5 text-white rounded text-[10px] font-medium"
                      style={{ background: PROVIDER_COLORS[src.provider] ?? '#6b7280' }}>
                      {src.provider}
                    </span>
                    <span className="text-gray-700 truncate font-medium">{src.name}</span>
                  </div>
                  <div className="text-gray-500">{src.images?.length ?? 0} images</div>
                  {src.url && (
                    <a href={src.url} target="_blank" rel="noopener noreferrer"
                      className="text-blue-500 hover:underline truncate block mt-1">
                      {src.url.length > 50 ? src.url.slice(0, 50) + '…' : src.url}
                    </a>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Lightbox */}
      {activeImage && (
        <div className="fixed inset-0 bg-black/80 z-[2000] flex items-center justify-center"
          onClick={() => setActiveImage(null)}>
          <div className="relative max-w-2xl max-h-[90vh]" onClick={e => e.stopPropagation()}>
            <img
              src={activeImage.local_path?.startsWith('../') ? activeImage.local_path.replace('../data/seoul/', '/images/') : activeImage.local_path || activeImage.image_url}
              alt=""
              className="max-w-full max-h-[80vh] object-contain rounded"
            />
            <div className="text-center mt-2">
              <span className="px-2 py-0.5 text-xs text-white rounded-full"
                style={{ background: PROVIDER_COLORS[activeImage.provider] ?? '#6b7280' }}>
                {activeImage.provider}
              </span>
            </div>
            <button className="absolute top-2 right-2 text-white bg-black/50 rounded-full w-8 h-8 flex items-center justify-center"
              onClick={() => setActiveImage(null)}>✕</button>
          </div>
        </div>
      )}
    </div>
  )
}
