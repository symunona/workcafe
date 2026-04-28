import { useEffect, useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import type { CleanCafe } from '../types'
import { PROVIDER_COLORS } from '../utils'
import { useSnapshot } from './SnapshotSelector'

interface Props {
  cafeId: string
  onClose: () => void
  activeTags?: Set<string>
  starredTags?: Set<string>
  isMobile?: boolean
}

export function CleanCafeDetailsPane({ cafeId, onClose, activeTags, starredTags, isMobile }: Props) {
  const [cafe, setCafe] = useState<CleanCafe | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [tagFilter, setTagFilter] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  const navigate = useNavigate()
  const { snapshot, apiUrl } = useSnapshot()

  useEffect(() => {
    setLoading(true)
    setCafe(null)
    setError(false)
    setTagFilter(null)
    setExpanded(false)
    fetch(apiUrl(`/api/clean_cafe?id=${encodeURIComponent(cafeId)}`))
      .then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json() })
      .then(data => { setCafe(data); setLoading(false) })
      .catch(() => { setError(true); setLoading(false) })
  }, [cafeId, snapshot]) // eslint-disable-line react-hooks/exhaustive-deps

  const allImages = cafe?.all_images ?? []
  const providers = cafe?.providers ?? []

  const tagCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const img of allImages) {
      for (const t of img.tags ?? []) {
        counts.set(t.tag, (counts.get(t.tag) ?? 0) + 1)
      }
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [allImages])

  // Sort tag chips: active > starred > count
  const sortedTagCounts = useMemo(() => {
    return [...tagCounts].sort(([tagA, cntA], [tagB, cntB]) => {
      const aActive = activeTags?.has(tagA) ? 2 : 0
      const aStarred = starredTags?.has(tagA) ? 1 : 0
      const bActive = activeTags?.has(tagB) ? 2 : 0
      const bStarred = starredTags?.has(tagB) ? 1 : 0
      const diff = (bActive + bStarred) - (aActive + aStarred)
      return diff !== 0 ? diff : cntB - cntA
    })
  }, [tagCounts, activeTags, starredTags])

  const sortedImages = useMemo(() => {
    const allTags = new Set([...(activeTags ?? []), ...(starredTags ?? [])])
    if (allTags.size) {
      const activeHit = activeTags?.size
        ? allImages.filter(img => img.tags?.some(t => activeTags.has(t.tag)))
        : []
      const starredHit = starredTags?.size
        ? allImages.filter(img => !activeHit.includes(img) && img.tags?.some(t => starredTags.has(t.tag)))
        : []
      const rest = allImages.filter(img => !activeHit.includes(img) && !starredHit.includes(img))

      if (activeTags?.size) {
        activeHit.sort((a, b) => {
          const sa = Math.max(0, ...(a.tags?.filter(t => activeTags.has(t.tag)).map(t => t.score) ?? []))
          const sb = Math.max(0, ...(b.tags?.filter(t => activeTags.has(t.tag)).map(t => t.score) ?? []))
          return sb - sa
        })
      }
      if (starredTags?.size) {
        starredHit.sort((a, b) => {
          const sa = Math.max(0, ...(a.tags?.filter(t => starredTags.has(t.tag)).map(t => t.score) ?? []))
          const sb = Math.max(0, ...(b.tags?.filter(t => starredTags.has(t.tag)).map(t => t.score) ?? []))
          return sb - sa
        })
      }
      return [...activeHit, ...starredHit, ...rest]
    }
    const tagged = allImages.filter(img => img.tags?.length)
    const untagged = allImages.filter(img => !img.tags?.length)
    return [...tagged, ...untagged]
  }, [allImages, activeTags, starredTags])

  const sampledImages = useMemo(() => {
    const pool = tagFilter ? sortedImages.filter(img => img.tags?.some(t => t.tag === tagFilter)) : sortedImages
    const result: typeof pool = []
    const usedProviders = new Set<string>()
    for (const img of pool) {
      if (!usedProviders.has(img.provider)) { result.push(img); usedProviders.add(img.provider) }
    }
    for (const img of pool) {
      if (result.length >= 6) break
      if (!result.includes(img)) result.push(img)
    }
    return result
  }, [sortedImages, tagFilter])

  const containerClass = isMobile
    ? `fixed bottom-0 left-0 right-0 z-[1000] bg-white shadow-xl flex flex-col rounded-t-2xl transition-all duration-300 ${expanded ? 'h-[95vh]' : 'h-[58vh]'}`
    : 'fixed left-0 top-0 h-full w-96 bg-white shadow-xl z-[1000] flex flex-col overflow-hidden'

  if (loading) return (
    <div className={containerClass}>
      {isMobile && (
        <div className="flex justify-center pt-2 pb-1 cursor-pointer" onClick={() => setExpanded(e => !e)}>
          <div className="w-10 h-1 bg-gray-300 rounded-full" />
        </div>
      )}
      <div className="flex-1 flex items-center justify-center">
        <div className="text-gray-500">Loading...</div>
      </div>
    </div>
  )

  if (error || !cafe) return (
    <div className={containerClass}>
      {isMobile && (
        <div className="flex justify-center pt-2 pb-1 cursor-pointer" onClick={() => setExpanded(e => !e)}>
          <div className="w-10 h-1 bg-gray-300 rounded-full" />
        </div>
      )}
      <div className="p-4 flex items-center justify-between border-b">
        <span className="text-sm text-gray-500 font-mono truncate">{cafeId}</span>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl flex-shrink-0 ml-2">✕</button>
      </div>
      <div className="flex-1 flex flex-col items-center justify-center gap-3 p-6 text-center">
        <span className="text-3xl">🔍</span>
        <p className="text-sm text-gray-500">Not found in this snapshot.</p>
        <p className="text-xs text-gray-400">Switch to Live to see full data.</p>
      </div>
    </div>
  )

  let tel = ''
  let bizhour = ''
  let homePage = ''

  if (cafe.sources) {
    const naver = cafe.sources.find(s => s.provider === 'naver')
    const kakao = cafe.sources.find(s => s.provider === 'kakao')

    if (naver && naver.metadata) {
      const meta = naver.metadata as any
      tel = meta.tel || meta.telDisplay || ''
      bizhour = meta.bizhourInfo || ''
      homePage = meta.homePage || meta.website || ''
    }
    if (kakao && kakao.metadata) {
      const meta = kakao.metadata as any
      if (!tel) tel = meta.phone || ''
      if (!homePage) homePage = meta.website || meta.homepage || ''
    }
  }

  return (
    <div className={containerClass}>
      {/* Mobile drag handle */}
      {isMobile && (
        <div
          className="flex justify-center pt-2 pb-1 cursor-pointer flex-shrink-0"
          onClick={() => setExpanded(e => !e)}
        >
          <div className="w-10 h-1 bg-gray-300 rounded-full" />
        </div>
      )}

      {/* Header */}
      <div className="px-4 py-3 shadow-sm bg-gray-50 flex items-start gap-2 z-10 flex-shrink-0">
        <div className="flex-1 min-w-0">
          <h2 className="font-bold text-lg truncate leading-tight">{cafe.english_name || cafe.name}</h2>
          {cafe.english_name && <p className="text-sm text-gray-400 truncate">{cafe.name}</p>}
          {cafe.chain_name && (
            <span className="inline-block mt-1 px-2 py-0.5 text-xs bg-amber-100 text-amber-700 rounded-full">
              {cafe.chain_name_english || cafe.chain_name}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          {isMobile && (
            <button
              onClick={() => navigate(`/cafe/${cafe.id}?source=all`)}
              className="text-blue-500 text-sm px-2 py-1 hover:bg-blue-50 rounded-lg"
            >
              Details
            </button>
          )}
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl w-8 h-8 flex items-center justify-center">✕</button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Metadata */}
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

        {/* Website */}
        {homePage && (
          <div className="px-4 pt-3">
            <a
              href={homePage}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 px-3 py-2 bg-blue-50 border border-blue-200 rounded-lg text-sm text-blue-700 hover:bg-blue-100 hover:border-blue-300 transition-colors"
            >
              <span>🌐</span>
              <span className="truncate flex-1">{homePage.replace(/^https?:\/\//, '').replace(/\/$/, '')}</span>
              <span className="text-blue-400 shrink-0">↗</span>
            </a>
          </div>
        )}

        {/* Hours + Phone */}
        {(tel || bizhour) && (
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
                <a href={`tel:${tel}`} className="hover:underline">{tel}</a>
              </div>
            )}
          </div>
        )}

        {/* Image gallery */}
        {allImages.length > 0 && (
          <div className="px-4 pt-3">
            <div className="flex items-baseline justify-between mb-2">
              <p className="text-xs text-gray-500 font-semibold uppercase tracking-wider">
                Gallery {tagFilter ? `· ${sampledImages.length} with "${tagFilter}"` : `(${allImages.length})`}
              </p>
              {tagFilter && (
                <button className="text-xs text-blue-500 hover:underline" onClick={() => setTagFilter(null)}>clear</button>
              )}
            </div>
            {sortedTagCounts.length > 0 && (
              <div className="flex flex-wrap gap-1 mb-3">
                {sortedTagCounts.map(([tag, count]) => {
                  const isActive = activeTags?.has(tag)
                  const isStarred = starredTags?.has(tag)
                  return (
                    <button key={tag}
                      onClick={() => setTagFilter(tagFilter === tag ? null : tag)}
                      className={`px-1.5 py-0.5 text-[10px] rounded-full border transition-colors ${
                        tagFilter === tag
                          ? 'bg-blue-600 text-white border-blue-600'
                          : isActive
                          ? 'bg-blue-100 text-blue-700 border-blue-300'
                          : isStarred
                          ? 'bg-amber-50 text-amber-700 border-amber-200'
                          : 'bg-gray-50 text-gray-600 border-gray-200 hover:border-blue-300'
                      }`}>
                      {isStarred && !isActive && <span className="text-amber-400">★</span>} {tag} <span className={tagFilter === tag ? 'text-blue-200' : 'text-gray-400'}>{count}</span>
                    </button>
                  )
                })}
              </div>
            )}
            <div className={`grid gap-2 ${isMobile ? 'grid-cols-2' : 'grid-cols-2'}`}>
              {sampledImages.map((img, i) => {
                const originalIndex = allImages.indexOf(img)
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
                    {img.tags?.length > 0 && (
                      <div className="absolute top-1 left-1 flex flex-wrap gap-0.5 max-w-[90%]">
                        {img.tags.slice(0, 3).map(t => (
                          <span key={t.tag} className="text-[9px] bg-black/65 text-white px-1 py-0.5 rounded-full backdrop-blur-sm leading-tight">
                            {t.tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Sources */}
        {cafe.sources && cafe.sources.length > 0 && (
          <div className="px-4 pt-4 pb-4">
            <p className="text-xs text-gray-500 mb-2 font-medium flex justify-between items-center">
              <span>Sources ({cafe.sources.length})</span>
              <button onClick={() => navigate(`/cafe/${cafe.id}?source=all`)} className="text-blue-500 hover:underline">Details →</button>
            </p>
            <div className="space-y-2">
              {cafe.sources.map(src => (
                <div key={src.id} className="p-2 text-xs cursor-pointer hover:bg-gray-50 rounded-lg"
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
