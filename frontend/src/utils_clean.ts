import L from 'leaflet'
import { PROVIDER_COLORS } from './utils'

export const CHAIN_COLORS: Record<string, string> = {
  "Starbucks": "#00704A",
  "Mega Coffee": "#FFC72C",
  "Ediya Coffee": "#002A5C",
  "A Twosome Place": "#D9182D",
  "Hollys Coffee": "#B41E22",
  "Compose Coffee": "#FEE100",
  "Paik's Coffee": "#00205B",
  "The Venti": "#6A2B86",
  "The Coffee Bean & Tea Leaf": "#4B2A20",
  "Caffe Pascucci": "#D11032",
  "Angel-in-us": "#B28F5A",
  "Yoger Presso": "#D6273C",
  "Tom N Toms": "#4A2B23",
  "Paul Bassett": "#E02B20",
  "Caffe Bene": "#795231",
  "Cafe Droptop": "#221E1F"
}

/**
 * Create a Leaflet DivIcon showing a pie-chart circle for multiple providers.
 * 1 provider: solid circle
 * 2+ providers: equal slices
 * Outer ring: Chain color if available, else black if has images.
 */
export function makePieIcon(providers: string[], size = 10, hasImages = false, chainName?: string): L.DivIcon {
  const colors = providers.map(p => PROVIDER_COLORS[p] ?? '#6b7280')
  
  // If chain, use its color. Else black if images.
  const chainColor = chainName ? CHAIN_COLORS[chainName] : undefined;
  const showRing = !!chainColor || hasImages;
  const ringColor = chainColor || "black";
  const border = showRing ? 2 : 1;
  const r = size / 2
  const cx = r
  const cy = r
  const innerR = r - border

  let slices = ''
  if (colors.length === 1) {
    slices = `<circle cx="${cx}" cy="${cy}" r="${innerR}" fill="${colors[0]}" />`
  } else {
    const n = colors.length
    const step = (2 * Math.PI) / n
    for (let i = 0; i < n; i++) {
      const a1 = i * step - Math.PI / 2
      const a2 = (i + 1) * step - Math.PI / 2
      const x1 = cx + innerR * Math.cos(a1)
      const y1 = cy + innerR * Math.sin(a1)
      const x2 = cx + innerR * Math.cos(a2)
      const y2 = cy + innerR * Math.sin(a2)
      const large = step > Math.PI ? 1 : 0
      slices += `<path d="M${cx},${cy} L${x1},${y1} A${innerR},${innerR} 0 ${large},1 ${x2},${y2} Z" fill="${colors[i]}" />`
    }
  }

  const ring = showRing
    ? `<circle cx="${cx}" cy="${cy}" r="${r - 0.5}" fill="none" stroke="${ringColor}" stroke-width="${border}" />`
    : ''

  const svg = `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg">
    ${slices}${ring}
  </svg>`

  return L.divIcon({
    html: svg,
    className: '',
    iconSize: [size, size],
    iconAnchor: [r, r],
  })
}

export function cleanCafeImageCount(cafe: { image_count: number }): number {
  return cafe.image_count ?? 0
}
