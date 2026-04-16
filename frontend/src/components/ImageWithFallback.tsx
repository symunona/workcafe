import { useState } from 'react'
import type { ImagePair } from '../utils'

interface ImageWithFallbackProps {
  pair: ImagePair
  alt: string
  className?: string
  onClick?: (e: React.MouseEvent) => void
  onLoad?: (e: React.SyntheticEvent<HTMLImageElement>) => void
}

export function ImageWithFallback({ pair, alt, className, onClick, onLoad }: ImageWithFallbackProps) {
  const [usedFallback, setUsedFallback] = useState(false)
  const [failedFallback, setFailedFallback] = useState(false)

  const handleError = () => {
    if (!usedFallback && pair.fallback) {
      setUsedFallback(true)
    } else {
      setFailedFallback(true)
    }
  }

  const src = usedFallback && pair.fallback ? pair.fallback : pair.src

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      {failedFallback ? (
        <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f3f4f6', color: '#9ca3af', fontSize: '13px' }}>
          Image unavailable
        </div>
      ) : (
        <img
          src={src}
          alt={alt}
          className={className}
          onClick={onClick}
          onLoad={onLoad}
          onError={handleError}
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
        />
      )}
      {usedFallback && !failedFallback && (
        <span style={{
          position: 'absolute', top: '6px', left: '6px',
          background: 'rgba(239,68,68,0.85)', color: '#fff',
          fontSize: '10px', fontWeight: 600, padding: '2px 6px',
          borderRadius: '4px', pointerEvents: 'none', letterSpacing: '0.02em'
        }}>
          404 local img
        </span>
      )}
    </div>
  )
}
