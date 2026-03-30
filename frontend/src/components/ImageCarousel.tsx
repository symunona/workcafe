import { useState } from 'react'
import { ExpandIcon } from './Icons'

interface ImageCarouselProps {
  images: string[]
  alt: string
  onFullScreen?: (index: number) => void
}

export function ImageCarousel({ images, alt, onFullScreen }: ImageCarouselProps) {
  const [idx, setIdx] = useState(0)
  
  if (images.length === 0) return null

  return (
    <div 
      className={`cafe-popup-img carousel ${onFullScreen ? 'cursor-pointer' : ''}`}
      onClick={(e) => {
        if (onFullScreen) {
          e.stopPropagation();
          onFullScreen(idx);
        }
      }}
    >
      <img src={images[idx]} alt={alt} />
      
      {images.length > 1 && (
        <>
          <button 
            className="carousel-btn prev" 
            onClick={e => { e.stopPropagation(); setIdx(i => (i - 1 + images.length) % images.length) }}
          >
            ‹
          </button>
          <button 
            className="carousel-btn next" 
            onClick={e => { e.stopPropagation(); setIdx(i => (i + 1) % images.length) }}
          >
            ›
          </button>
          <div className="carousel-counter">{idx + 1} / {images.length}</div>
        </>
      )}

      {onFullScreen && (
        <button 
          className="carousel-fullscreen-btn" 
          onClick={(e) => {
            e.stopPropagation();
            onFullScreen(idx);
          }}
          title="View full screen"
        >
          <ExpandIcon />
        </button>
      )}
    </div>
  )
}
