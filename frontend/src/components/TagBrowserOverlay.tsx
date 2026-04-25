import { useEffect, useState, useRef, useCallback } from 'react'
import { useSnapshot, SnapshotSelector } from './SnapshotSelector'

interface TagCount {
  tag: string
  count: number
}

interface TagImage {
  image_id: number
  cafe_id: string
  local_path: string
  score: number
}

const THRESHOLDS: { label: string; value: number; color: string }[] = [
  { label: 'A', value: 0.22, color: '#ef9a9a' },
  { label: 'B', value: 0.25, color: '#ffcc80' },
  { label: 'C', value: 0.27, color: '#a5d6a7' },
]

interface Props {
  onClose: () => void
}

export function TagBrowserOverlay({ onClose }: Props) {
  const { snapshot, setSnapshot, apiUrl } = useSnapshot()
  const [tags, setTags] = useState<TagCount[]>([])
  const [selectedTag, setSelectedTag] = useState<string>('')
  const [images, setImages] = useState<TagImage[]>([])
  const [loadingTags, setLoadingTags] = useState(true)
  const [loadingImages, setLoadingImages] = useState(false)
  const [threshold, setThreshold] = useState(0.22)
  const thresholdRef = useRef(threshold)
  thresholdRef.current = threshold
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null)

  // Close on Escape / arrow nav
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { if (lightboxIndex != null) closeLightbox(); else onClose() }
      if (e.key === 'ArrowLeft') prevImage()
      if (e.key === 'ArrowRight') nextImage()
    }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [onClose, lightboxIndex, closeLightbox, prevImage, nextImage])

  // Load tag list when snapshot changes
  useEffect(() => {
    setLoadingTags(true)
    setImages([])
    fetch(apiUrl('/api/image-tags'))
      .then(r => r.ok ? r.json() : [])
      .then((data: TagCount[]) => {
        setTags(data ?? [])
        if (data?.length > 0 && !selectedTag) setSelectedTag(data[0].tag)
      })
      .catch(() => setTags([]))
      .finally(() => setLoadingTags(false))
  }, [snapshot]) // eslint-disable-line react-hooks/exhaustive-deps

  // Load images when tag or snapshot changes
  useEffect(() => {
    if (!selectedTag) return
    setLoadingImages(true)
    setImages([])
    fetch(apiUrl(`/api/tag-images?tag=${encodeURIComponent(selectedTag)}`))
      .then(r => r.ok ? r.json() : [])
      .then((data: TagImage[]) => setImages(data ?? []))
      .catch(() => setImages([]))
      .finally(() => setLoadingImages(false))
  }, [selectedTag, snapshot]) // eslint-disable-line react-hooks/exhaustive-deps

  const aboveThreshold = images.filter(i => i.score >= threshold)
  const belowThreshold = images.filter(i => i.score < threshold)
  const displayImages = [...aboveThreshold, ...belowThreshold]

  const closeLightbox = useCallback(() => setLightboxIndex(null), [])
  const prevImage = useCallback(() => setLightboxIndex(i => i != null ? Math.max(0, i - 1) : null), [])
  const nextImage = useCallback(() => setLightboxIndex(i => i != null ? Math.min(displayImages.length - 1, i + 1) : null), [displayImages.length])

  // Find the threshold divider position in sorted-by-score list
  const thresholdInfo = THRESHOLDS.find(t => t.value === threshold)

  return (
    <div className="fixed inset-0 z-[900] bg-gray-950 flex flex-col">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-4 py-2.5 bg-gray-900 border-b border-gray-800 shrink-0">
        <button
          onClick={onClose}
          className="text-gray-400 hover:text-white text-lg leading-none px-1"
          title="Close (Esc)"
        >✕</button>
        <span className="text-white font-semibold text-sm">Tag Browser</span>
        <div className="h-4 w-px bg-gray-700" />

        {/* Threshold selector */}
        <div className="flex items-center gap-1">
          <span className="text-gray-500 text-xs mr-1">Threshold:</span>
          {THRESHOLDS.map(t => (
            <button
              key={t.label}
              onClick={() => setThreshold(t.value)}
              className="px-2.5 py-1 rounded text-xs font-mono font-semibold transition-all"
              style={{
                background: threshold === t.value ? t.color : 'transparent',
                color: threshold === t.value ? '#111' : '#9ca3af',
                border: `1px solid ${threshold === t.value ? t.color : '#374151'}`,
              }}
            >
              {t.label} ≥{t.value}
            </button>
          ))}
        </div>

        <div className="ml-auto">
          <SnapshotSelector snapshot={snapshot} setSnapshot={setSnapshot} />
        </div>
      </div>

      <div className="flex flex-1 min-h-0">
        {/* Sidebar — tag list */}
        <div className="w-52 shrink-0 bg-gray-900 border-r border-gray-800 overflow-y-auto">
          {loadingTags ? (
            <div className="text-gray-500 text-xs p-4">Loading…</div>
          ) : tags.length === 0 ? (
            <div className="text-gray-500 text-xs p-4">No image_tags in this snapshot.</div>
          ) : (
            <div className="py-2">
              {tags.map(({ tag, count }) => (
                <button
                  key={tag}
                  onClick={() => setSelectedTag(tag)}
                  className={`w-full text-left px-4 py-2 text-sm flex items-center justify-between gap-2 transition-colors ${
                    selectedTag === tag
                      ? 'bg-blue-600 text-white'
                      : 'text-gray-300 hover:bg-gray-800'
                  }`}
                >
                  <span className="truncate">{tag}</span>
                  <span className={`text-xs shrink-0 ${selectedTag === tag ? 'text-blue-200' : 'text-gray-500'}`}>{count}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Main image grid */}
        <div className="flex-1 overflow-y-auto">
          {loadingImages ? (
            <div className="flex items-center justify-center h-32 text-gray-500 text-sm">Loading…</div>
          ) : images.length === 0 ? (
            <div className="flex items-center justify-center h-32 text-gray-500 text-sm">
              {selectedTag ? 'No images for this tag in current snapshot.' : 'Select a tag.'}
            </div>
          ) : (
            <div className="p-4">
              {/* Stats bar */}
              <div className="text-xs text-gray-500 mb-3 flex items-center gap-4">
                <span>
                  <span className="text-white font-medium">{aboveThreshold.length}</span> above threshold
                  <span style={{ color: thresholdInfo?.color }} className="ml-1 font-mono">({threshold})</span>
                </span>
                <span>{belowThreshold.length} below</span>
                <span>{images.length} total</span>
                <span className="text-gray-600">sorted by score ↓</span>
              </div>

              {/* Images above threshold */}
              {aboveThreshold.length > 0 && (
                <div className="flex flex-wrap gap-2 mb-4">
                  {aboveThreshold.map((img, i) => (
                    <ImageCard key={img.image_id} img={img} dimmed={false} threshold={threshold}
                      onClick={() => setLightboxIndex(i)} />
                  ))}
                </div>
              )}

              {/* Threshold divider */}
              {belowThreshold.length > 0 && (
                <div className="flex items-center gap-3 my-4">
                  <div className="flex-1 h-px" style={{ background: thresholdInfo?.color ?? '#888' }} />
                  <span
                    className="text-xs font-mono font-semibold px-2 py-0.5 rounded"
                    style={{ background: thresholdInfo?.color, color: '#111' }}
                  >
                    Exp {thresholdInfo?.label} cutoff ≥{threshold}
                  </span>
                  <div className="flex-1 h-px" style={{ background: thresholdInfo?.color ?? '#888' }} />
                </div>
              )}

              {/* Images below threshold */}
              {belowThreshold.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {belowThreshold.map((img, i) => (
                    <ImageCard key={img.image_id} img={img} dimmed threshold={threshold}
                      onClick={() => setLightboxIndex(aboveThreshold.length + i)} />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Lightbox */}
      {lightboxIndex != null && displayImages[lightboxIndex] && (
        <Lightbox
          images={displayImages}
          index={lightboxIndex}
          threshold={threshold}
          onClose={closeLightbox}
          onPrev={prevImage}
          onNext={nextImage}
        />
      )}
    </div>
  )
}

function Lightbox({ images, index, threshold, onClose, onPrev, onNext }: {
  images: TagImage[]
  index: number
  threshold: number
  onClose: () => void
  onPrev: () => void
  onNext: () => void
}) {
  const img = images[index]
  const scoreColor = img.score >= 0.27 ? '#a5d6a7' : img.score >= 0.25 ? '#ffcc80' : '#ef9a9a'
  const hasPrev = index > 0
  const hasNext = index < images.length - 1

  return (
    <div
      className="fixed inset-0 z-[1000] bg-black/90 flex items-center justify-center"
      onClick={onClose}
    >
      {/* Prev button */}
      {hasPrev && (
        <button
          className="absolute left-4 top-1/2 -translate-y-1/2 text-white/70 hover:text-white text-4xl px-3 py-2 rounded-full bg-black/40 hover:bg-black/70 z-10"
          onClick={e => { e.stopPropagation(); onPrev() }}
        >‹</button>
      )}

      {/* Image */}
      <div
        className="relative max-w-[90vw] max-h-[90vh] flex items-center justify-center"
        onClick={e => e.stopPropagation()}
      >
        <img
          src={img.local_path}
          alt=""
          className="max-w-[90vw] max-h-[90vh] object-contain rounded shadow-2xl"
        />
        {/* Score badge */}
        <div
          className="absolute bottom-2 right-2 text-xs font-mono font-bold px-2 py-1 rounded shadow"
          style={{ background: scoreColor, color: '#111' }}
        >
          {img.score.toFixed(3)}
        </div>
        {/* Counter */}
        <div className="absolute top-2 right-2 text-xs text-white/60 bg-black/50 px-2 py-1 rounded font-mono">
          {index + 1} / {images.length}
        </div>
        {/* Cafe id */}
        <div className="absolute top-2 left-2 text-xs text-white/50 bg-black/50 px-2 py-1 rounded font-mono truncate max-w-xs">
          {img.cafe_id}
        </div>
      </div>

      {/* Next button */}
      {hasNext && (
        <button
          className="absolute right-4 top-1/2 -translate-y-1/2 text-white/70 hover:text-white text-4xl px-3 py-2 rounded-full bg-black/40 hover:bg-black/70 z-10"
          onClick={e => { e.stopPropagation(); onNext() }}
        >›</button>
      )}

      {/* Close */}
      <button
        className="absolute top-4 right-4 text-white/60 hover:text-white text-2xl px-2"
        onClick={onClose}
      >✕</button>
    </div>
  )
}

function ImageCard({ img, dimmed, onClick }: { img: TagImage; dimmed: boolean; threshold?: number; onClick?: () => void }) {
  const [error, setError] = useState(false)
  const scoreColor = img.score >= 0.27 ? '#a5d6a7' : img.score >= 0.25 ? '#ffcc80' : '#ef9a9a'

  if (error) return null

  return (
    <div
      className="relative rounded overflow-hidden shrink-0 transition-opacity cursor-pointer hover:ring-2 hover:ring-white/40"
      style={{ width: 160, height: 160, opacity: dimmed ? 0.35 : 1 }}
      onClick={onClick}
    >
      <img
        src={img.local_path}
        alt=""
        className="w-full h-full object-cover"
        onError={() => setError(true)}
        loading="lazy"
      />
      {/* Score badge */}
      <div
        className="absolute bottom-1 right-1 text-xs font-mono font-bold px-1.5 py-0.5 rounded"
        style={{ background: scoreColor, color: '#111' }}
      >
        {img.score.toFixed(3)}
      </div>
    </div>
  )
}
