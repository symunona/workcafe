import L from 'leaflet'
import { PROVIDER_COLORS } from './utils'

export const CAFE_BROWN = '#9B7653'

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

export function makePieIcon(
  providers: string[],
  size = 10,
  hasImages = false,
  chainName?: string,
  showSourceColors = true,
  showBrandColors = true,
  showImageRing = false,
): L.DivIcon {
  const r = size / 2
  const cx = r
  const cy = r
  const outerR = r
  const ringActive = hasImages && showImageRing
  const strokeAttr = ringActive ? `stroke="black" stroke-width="1.5"` : `stroke="none"`

  if (!showSourceColors) {
    const chainColor = chainName ? CHAIN_COLORS[chainName] : undefined
    const fill = (showBrandColors && chainColor) ? chainColor : CAFE_BROWN
    const svg = `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg">
      <circle cx="${cx}" cy="${cy}" r="${outerR - 0.75}" fill="${fill}" ${strokeAttr} />
    </svg>`
    return L.divIcon({ html: svg, className: '', iconSize: [size, size], iconAnchor: [r, r] })
  }

  const colors = providers.map(p => PROVIDER_COLORS[p] ?? '#6b7280')
  const border = size > 12 ? 2.5 : 1.5
  const innerR = r - border

  let slices = ''
  if (colors.length === 1) {
    slices = `<circle cx="${cx}" cy="${cy}" r="${outerR}" fill="${colors[0]}" />`
  } else {
    const n = colors.length
    const step = (2 * Math.PI) / n
    for (let i = 0; i < n; i++) {
      const a1 = i * step - Math.PI / 2
      const a2 = (i + 1) * step - Math.PI / 2
      const x1 = cx + outerR * Math.cos(a1)
      const y1 = cy + outerR * Math.sin(a1)
      const x2 = cx + outerR * Math.cos(a2)
      const y2 = cy + outerR * Math.sin(a2)
      const large = step > Math.PI ? 1 : 0
      slices += `<path d="M${cx},${cy} L${x1},${y1} A${outerR},${outerR} 0 ${large},1 ${x2},${y2} Z" fill="${colors[i]}" />`
    }
  }

  const chainColor = (showBrandColors && chainName) ? CHAIN_COLORS[chainName] : undefined
  const innerFill = chainColor || 'white'
  const innerStroke = ringActive ? `stroke="black" stroke-width="1"` : `stroke="none"`
  const innerCircle = `<circle cx="${cx}" cy="${cy}" r="${innerR}" fill="${innerFill}" ${innerStroke} />`

  const svg = `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg">
    ${slices}
    ${innerCircle}
  </svg>`

  return L.divIcon({ html: svg, className: '', iconSize: [size, size], iconAnchor: [r, r] })
}

export function cleanCafeImageCount(cafe: { image_count: number }): number {
  return cafe.image_count ?? 0
}
