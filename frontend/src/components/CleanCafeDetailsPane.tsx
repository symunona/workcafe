import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { CleanCafe } from '../types'
import { PROVIDER_COLORS } from '../utils'

interface Props {
  cafeId: string
  onClose: () => void
}

export function CleanCafeDetailsPane({ cafeId, onClose }: Props) {
  const [cafe, setCafe] = useState<CleanCafe | null>(null)
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  useEffect(() => {
    setLoading(true)
    setCafe(null)
    fetch(`/api/clean_cafe?id=${encodeURIComponent(cafeId)}`)
      .then(r => r.json())
      .then(data => { setCafe(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [cafeId])

  if (loading) return (
    <div className="fixed left-0 top-0 h-full w-96 bg-white shadow-xl z-[1000] flex items-center justify-center">
      <div className="text-gray-500">Loading...</div>
    </div>
  )

  if (!cafe) return null

  const allImages = cafe.all_images ?? []
  const providers = cafe.providers ?? []

  // Extract some useful metadata from sources
  let tel = ''
  let bizhour = ''
  let homePage = ''
  
  if (cafe.sources) {
    const naver = cafe.sources.find(s => s.provider === 'naver')
    const kakao = cafe.sources.find(s => s.provider === 'kakao')
    
    if (naver && naver.metadata) {
      const meta = naver.metadata as any;
      tel = meta.tel || meta.telDisplay || ''
      bizhour = meta.bizhourInfo || ''
      homePage = meta.homePage || ''
    } else if (kakao && kakao.metadata) {
      const meta = kakao.metadata as any;
      tel = meta.phone || ''
      homePage = meta.homepage || ''
    }
  }

  // Sample top 6 images from diff providers
  const sampledImages = []
  const usedProviders = new Set<string>()
  for (const img of allImages) {
    if (!usedProviders.has(img.provider)) {
      sampledImages.push(img)
      usedProviders.add(img.provider)
    }
  }
  for (const img of allImages) {
    if (sampledImages.length >= 6) break
    if (!sampledImages.includes(img)) sampledImages.push(img)
  }

  return (
    <div className="fixed left-0 top-0 h-full w-96 bg-white shadow-xl z-[1000] flex flex-col overflow-hidden">
      {/* Header */}
      <div className="p-4 shadow-sm bg-gray-50 flex items-start gap-2 z-10">
        <div className="flex-1 min-w-0">
          <h2 className="font-bold text-lg truncate">{cafe.english_name || cafe.name}</h2>
          {cafe.english_name && <p className="text-sm text-gray-400 truncate">{cafe.name}</p>}
          {cafe.chain_name && (
            <span className="inline-block mt-1 px-2 py-0.5 text-xs bg-amber-100 text-amber-700 rounded-full">
              {cafe.chain_name_english || cafe.chain_name}
            </span>
          )}
        </div>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl flex-shrink-0">✕</button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Metadata Top */}
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

        {/* Useful Metadata */}
        {(tel || bizhour || homePage) && (
          <div className="px-4 pt-3 space-y-1">
            {bizhour && (
              <div className="flex items-start gap-2 text-sm text-gray-600">
                <span className="text-gray-400 mt-0.5">🕒</span>
                <span className="flex-1 whitespace-pre-wrap">{bizhour.replace(/\|/g, '\n')}</span>
              </div>
            )}
            {tel && (
              <div className="flex items-center gap-2 text-sm text-gray-600">
                <span className="text-gray-400">📞</span>
                <span>{tel}</span>
              </div>
            )}
            {homePage && (
              <div className="flex items-center gap-2 text-sm text-gray-600 truncate">
                <span className="text-gray-400">🔗</span>
                <a href={homePage} target="_blank" rel="noopener noreferrer" className="text-blue-500 hover:underline truncate">
                  {homePage}
                </a>
              </div>
            )}
          </div>
        )}

        {/* Image gallery (Top 6 sampled) */}
        {sampledImages.length > 0 && (
          <div className="px-4 pt-3">
            <p className="text-xs text-gray-500 mb-3 font-semibold uppercase tracking-wider">Gallery</p>
            <div className="grid grid-cols-2 gap-2">
              {sampledImages.map((img, i) => {
                const originalIndex = allImages.indexOf(img);
                return (
                <div key={i} className="relative aspect-video cursor-pointer rounded-lg overflow-hidden shadow-sm hover:shadow-md transition-shadow group"
                  onClick={() => navigate(`/cafe/${cafe.id}?source=all&image=${originalIndex}`)}>
                  <img
                    src={img.local_path?.startsWith('../') ? img.local_path.replace('../data/seoul/', '/images/') : img.local_path || img.image_url}
                    className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                    alt=""
                    loading="lazy"
                  />
                  <span className="absolute bottom-1 right-1 text-[10px] bg-black/60 text-white px-1.5 py-0.5 rounded backdrop-blur-sm">
                    {img.provider}
                  </span>
                </div>
              )})}
            </div>
          </div>
        )}

        {/* Sources (scrapers that found this cafe) */}
        {cafe.sources && cafe.sources.length > 0 && (
          <div className="px-4 pt-4">
            <p className="text-xs text-gray-500 mb-2 font-medium flex justify-between items-center">
              <span>Sources ({cafe.sources.length})</span>
              <button onClick={() => navigate(`/cafe/${cafe.id}?source=all`)} className="text-blue-500 hover:underline">Details →</button>
            </p>
            <div className="space-y-2">
              {cafe.sources.map(src => (
                <div key={src.id} className="p-2 text-xs cursor-pointer hover:bg-gray-50"
                     onClick={() => navigate(`/cafe/${cafe.id}?source=${src.id}`)}>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="px-1.5 py-0.5 text-white rounded text-[10px] font-medium"
                      style={{ background: PROVIDER_COLORS[src.provider] ?? '#6b7280' }}>
                      {src.provider}
                    </span>
                    <span className="text-gray-700 truncate font-medium">{src.name}</span>
                  </div>
                  <div className="text-gray-500">{src.images?.length ?? 0} images</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
