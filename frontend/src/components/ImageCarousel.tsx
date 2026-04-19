import { useState, useEffect } from 'react'
import { ExpandIcon } from './Icons'
import type { ImagePair } from '../utils'
import { ImageWithFallback } from './ImageWithFallback'

interface ImageCarouselProps {
  images: ImagePair[]
  alt: string
  onFullScreen?: (index: number) => void
}

export function ImageCarousel({ images, alt, onFullScreen }: ImageCarouselProps) {
  const [idx, setIdx] = useState(0)
  const [failedSet, setFailedSet] = useState<Set<number>>(new Set())

  useEffect(() => {
    setIdx(0)
    setFailedSet(new Set())
  }, [images])

  const valid = images.map((img, i) => ({ img, i })).filter(({ i }) => !failedSet.has(i))

  if (valid.length === 0) return null

  const clampedIdx = Math.min(idx, valid.length - 1)
  const current = valid[clampedIdx]

  return (
    <div
      className={`cafe-popup-img carousel ${onFullScreen ? 'cursor-pointer' : ''}`}
      onClick={(e) => {
        if (onFullScreen) {
          e.stopPropagation()
          onFullScreen(clampedIdx)
        }
      }}
    >
      <ImageWithFallback
        key={current.i}
        pair={current.img}
        alt={alt}
        onPermanentFailure={() => setFailedSet(prev => new Set([...prev, current.i]))}
      />

      {valid.length > 1 && (
        <>
          <button
            className="carousel-btn prev"
            onClick={e => { e.stopPropagation(); setIdx(i => (i - 1 + valid.length) % valid.length) }}
          >
            ‹
          </button>
          <button
            className="carousel-btn next"
            onClick={e => { e.stopPropagation(); setIdx(i => (i + 1) % valid.length) }}
          >
            ›
          </button>
          <div className="carousel-counter">{clampedIdx + 1} / {valid.length}</div>
        </>
      )}

      {onFullScreen && (
        <button
          className="carousel-fullscreen-btn"
          onClick={(e) => {
            e.stopPropagation()
            onFullScreen(clampedIdx)
          }}
          title="View full screen"
        >
          <ExpandIcon />
        </button>
      )}
    </div>
  )
}
