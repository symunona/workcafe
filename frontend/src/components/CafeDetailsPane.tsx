import type { Cafe } from '../types'
import { getMeta, getImages, providerColor } from '../utils'
import { ImageCarousel } from './ImageCarousel'
import { PhoneIcon, PinIcon, StarIcon, ArrowIcon, CloseIcon } from './Icons'

interface CafeDetailsPaneProps {
  cafe: Cafe
  onClose: () => void
  onFullScreenImage: (index: number) => void
}

export function CafeDetailsPane({ cafe, onClose, onFullScreenImage }: CafeDetailsPaneProps) {
  const meta = getMeta(cafe)
  const images = getImages(cafe)
  const categories = meta?.category ?? []
  const status = meta?.businessStatus?.status
  const isOpen = status?.code === 2

  return (
    <div className="cafe-details-pane">
      <button className="cafe-details-close" onClick={onClose} aria-label="Close">
        <CloseIcon />
      </button>

      <div className="cafe-details-image-container">
        <ImageCarousel key={cafe.id} images={images} alt={cafe.name} onFullScreen={onFullScreenImage} />
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
          <div className="cafe-details-row" style={{ marginTop: '16px', fontSize: '12px', color: '#9ca3af', display: 'flex', alignItems: 'center', flexWrap: 'wrap' }}>
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
