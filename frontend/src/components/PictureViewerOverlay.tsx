import { useState, useEffect, useRef } from 'react'
import type { Cafe } from '../types'
import { getImagePairs } from '../utils'
import { ImageWithFallback } from './ImageWithFallback'
import { CloseIcon } from './Icons'

interface PictureViewerOverlayProps {
  cafe: Cafe
  initialIndex: number
  onClose: () => void
}

export function PictureViewerOverlay({ cafe, initialIndex, onClose }: PictureViewerOverlayProps) {
  const images = getImagePairs(cafe)
  const [currentIndex, setCurrentIndex] = useState(initialIndex)
  const [imageMeta, setImageMeta] = useState<{ width: number; height: number } | null>(null)
  const galleryRef = useRef<HTMLDivElement>(null)

  const handlePrev = () => {
    setCurrentIndex((prev) => (prev - 1 + images.length) % images.length)
    setImageMeta(null)
  }

  const handleNext = () => {
    setCurrentIndex((prev) => (prev + 1) % images.length)
    setImageMeta(null)
  }

  const handleImageLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget
    setImageMeta({ width: img.naturalWidth, height: img.naturalHeight })
  }

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === 'ArrowLeft') handlePrev()
      if (e.key === 'ArrowRight') handleNext()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose, images.length])

  useEffect(() => {
    if (galleryRef.current) {
      const activeThumb = galleryRef.current.children[currentIndex] as HTMLElement
      if (activeThumb) {
        activeThumb.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' })
      }
    }
  }, [currentIndex])

  if (!images.length) return null

  const currentPair = images[currentIndex]

  return (
    <div className="picture-viewer-overlay">
      <div className="picture-viewer-main">
        <button className="picture-viewer-close" onClick={onClose} aria-label="Close">
          <CloseIcon />
        </button>

        <div className="picture-viewer-center">
          <ImageWithFallback
            pair={currentPair}
            alt={`${cafe.name} ${currentIndex + 1}`}
            className="picture-viewer-img"
            onLoad={handleImageLoad}
          />

          {images.length > 1 && (
            <>
              <button className="picture-viewer-btn prev" onClick={handlePrev}>‹</button>
              <button className="picture-viewer-btn next" onClick={handleNext}>›</button>
              <div className="picture-viewer-counter">{currentIndex + 1} / {images.length}</div>
            </>
          )}
        </div>

        {/* Thumbnail gallery below */}
        {images.length > 1 && (
          <div className="picture-viewer-gallery-wrapper">
            <div className="picture-viewer-gallery" ref={galleryRef}>
              {images.map((pair, idx) => (
                <img
                  key={idx}
                  src={pair.src}
                  alt={`Thumbnail ${idx + 1}`}
                  className={`picture-viewer-thumb ${idx === currentIndex ? 'active' : ''}`}
                  onClick={() => {
                    setCurrentIndex(idx)
                    setImageMeta(null)
                  }}
                />
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="picture-viewer-sidebar">
        <div className="cafe-details-body">
          <div className="cafe-details-name">Image Metadata</div>

          <div className="cafe-details-rows mt-4">
            <div className="cafe-details-row">
              <strong>Source:</strong> {cafe.provider}
            </div>
            <div className="cafe-details-row">
              <strong>Index:</strong> {currentIndex + 1} of {images.length}
            </div>
            {imageMeta && (
              <div className="cafe-details-row">
                <strong>Resolution:</strong> {imageMeta.width} × {imageMeta.height}
              </div>
            )}
            <div className="cafe-details-row" style={{ wordBreak: 'break-all' }}>
              <strong>URL:</strong> <a href={currentPair.src} target="_blank" rel="noopener noreferrer" className="cafe-popup-link" style={{marginTop: '4px'}}>{currentPair.src}</a>
            </div>
            {currentPair.fallback && (
              <div className="cafe-details-row" style={{ wordBreak: 'break-all' }}>
                <strong>CDN fallback:</strong> <a href={currentPair.fallback} target="_blank" rel="noopener noreferrer" className="cafe-popup-link" style={{marginTop: '4px'}}>{currentPair.fallback}</a>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
