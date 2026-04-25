import type { TagInfo } from '../types'

interface TaggedImageProps {
  src: string
  alt?: string
  className?: string
  imgClassName?: string
  hoveredTag?: string | null
  tags?: TagInfo[]
  onLoad?: React.ReactEventHandler<HTMLImageElement>
  loading?: 'lazy' | 'eager'
  style?: React.CSSProperties
}

/**
 * Image with SVG bounding-box overlay. When hoveredTag matches a tag that
 * has boxes, semi-transparent rectangles highlight the detected regions.
 * Backward compat: if tags is empty or tag has no boxes, renders plain img.
 */
export function TaggedImage({
  src, alt = '', className, imgClassName, hoveredTag, tags = [],
  onLoad, loading, style,
}: TaggedImageProps) {
  const activeBoxes: number[][] = hoveredTag
    ? (tags.find(t => t.tag === hoveredTag)?.boxes ?? []).filter(Boolean) as number[][]
    : []

  const hasBoxes = activeBoxes.length > 0

  if (!hasBoxes) {
    return (
      <img
        src={src} alt={alt}
        className={imgClassName ?? className}
        onLoad={onLoad}
        loading={loading}
        style={style}
      />
    )
  }

  return (
    <span className={`relative inline-block ${className ?? ''}`} style={style}>
      <img
        src={src} alt={alt}
        className={imgClassName}
        onLoad={onLoad}
        loading={loading}
        style={{ display: 'block' }}
      />
      <svg
        className="absolute inset-0 w-full h-full pointer-events-none"
        viewBox="0 0 1 1"
        preserveAspectRatio="none"
        aria-hidden
      >
        {activeBoxes.map(([x1, y1, x2, y2], i) => (
          <g key={i}>
            <rect
              x={x1} y={y1}
              width={x2 - x1} height={y2 - y1}
              fill="rgba(59,130,246,0.12)"
              stroke="#3b82f6"
              strokeWidth="0.008"
              rx="0.005"
            />
            <rect
              x={x1 + 0.003} y={y1 + 0.003}
              width={x2 - x1 - 0.006} height={y2 - y1 - 0.006}
              fill="none"
              stroke="rgba(255,255,255,0.5)"
              strokeWidth="0.003"
              rx="0.003"
            />
          </g>
        ))}
      </svg>
    </span>
  )
}
