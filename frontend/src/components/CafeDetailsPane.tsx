import type { Cafe } from '../types'
import { getMeta, getKakaoMeta, getImagePairs, providerColor } from '../utils'
import { ImageCarousel } from './ImageCarousel'
import { PhoneIcon, PinIcon, StarIcon, ArrowIcon, CloseIcon } from './Icons'

interface CafeDetailsPaneProps {
  cafe: Cafe
  onClose: () => void
  onFullScreenImage: (index: number) => void
}

function photoCountLine(cafe: Cafe, downloadedCount: number): string | null {
  const kakao = getKakaoMeta(cafe)
  const total = kakao?.photo_counts?.total
  const scraped = kakao?.scraped_photos ?? getMeta(cafe)?.scraped_photos
  if (total != null && total > 0) {
    return `📷 ${downloadedCount} downloaded of ${total.toLocaleString()} available`
  }
  if (scraped != null && scraped > downloadedCount) {
    return `📷 ${downloadedCount} shown · ${scraped.toLocaleString()} scraped`
  }
  return null
}

export function CafeDetailsPane({ cafe, onClose, onFullScreenImage }: CafeDetailsPaneProps) {
  const meta = getMeta(cafe)
  const images = getImagePairs(cafe)
  const categories = meta?.category ?? []
  const status = meta?.businessStatus?.status
  const isOpen = status?.code === 2

  return (
    <div className="cafe-details-pane">
      <button className="cafe-details-close" onClick={onClose} aria-label="Close">
        <CloseIcon />
      </button>

      <div className="cafe-details-image-container">
        {images.length > 0 ? (
          <ImageCarousel key={cafe.id} images={images} alt={cafe.name} onFullScreen={onFullScreenImage} />
        ) : (
          <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#9ca3af', fontSize: '14px' }}>
            No images available
          </div>
        )}
      </div>

      <div className="cafe-details-body">
        <div className="cafe-details-name">{cafe.name}</div>
        
        {categories.length > 0 && (
          <div className="cafe-details-tags">
            {categories.map(cat => (
              <span key={cat} className="cafe-details-tag">{cat}</span>
            ))}
          </div>
        )}

        <div className="cafe-details-rows">
          {status && (
            <div className={`cafe-details-status ${isOpen ? 'open' : 'closed'}`}>
              <span className="status-dot" />
              {status.text}
              {status.description && <span className="status-desc"> · {status.description}</span>}
            </div>
          )}
          {meta?.tel && <div className="cafe-details-row"><PhoneIcon />{meta.tel}</div>}
          {cafe.address && <div className="cafe-details-row"><PinIcon />{cafe.address}</div>}
          {meta?.reviewCount != null && (
            <div className="cafe-details-row"><StarIcon />{meta.reviewCount.toLocaleString()} reviews</div>
          )}
        </div>

        {cafe.url && (
          <a href={cafe.url} target="_blank" rel="noopener noreferrer" className="cafe-details-link">
            View on map
            <ArrowIcon />
          </a>
        )}

        <div className="cafe-details-meta">
          {(() => { const line = photoCountLine(cafe, images.length); return line && (
            <div style={{ fontSize: '12px', color: '#9ca3af', marginTop: '8px' }}>{line}</div>
          )})()}
          <div className="cafe-details-row" style={{ marginTop: '8px', fontSize: '12px', color: '#9ca3af', display: 'flex', alignItems: 'center', flexWrap: 'wrap' }}>
            Source:
            <span style={{ display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%', backgroundColor: providerColor(cafe.provider), margin: '0 4px 0 6px' }}></span>
            {cafe.provider}
            {cafe.scraped_at && ` • Scraped: ${new Date(cafe.scraped_at).toLocaleString()}`}
          </div>
        </div>
      </div>
    </div>
  )
}
