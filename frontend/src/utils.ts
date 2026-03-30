import type { Cafe, NaverMetadata, KakaoMetadata, GoogleMetadata } from './types'

export const PROVIDER_COLORS: Record<string, string> = {
  naver:      '#7c3aed', // purple
  osm:        '#0ea5e9', // sky blue
  google:     '#ea4335', // google red
  kakao:      '#f59e0b', // amber
  foursquare: '#10b981', // emerald
}

export function providerColor(provider: string) {
  return PROVIDER_COLORS[provider] ?? '#6b7280'
}

export function getMeta(cafe: Cafe): NaverMetadata | null {
  if (!cafe.metadata || cafe.provider !== 'naver') return null
  return cafe.metadata as unknown as NaverMetadata
}

export function getKakaoMeta(cafe: Cafe): KakaoMetadata | null {
  if (!cafe.metadata || cafe.provider !== 'kakao') return null
  return cafe.metadata as unknown as KakaoMetadata
}

export function getGoogleMeta(cafe: Cafe): GoogleMetadata | null {
  if (!cafe.metadata || cafe.provider !== 'google') return null
  return cafe.metadata as unknown as GoogleMetadata
}

export function getImages(cafe: Cafe): string[] {
  // Always prefer locally hosted images when available
  const anyMeta = cafe.metadata as { local_images?: string[], local_image_paths?: string[] } | null
  if (anyMeta?.local_images?.length) return anyMeta.local_images
  if (anyMeta?.local_image_paths?.length) {
    return anyMeta.local_image_paths.map(p => {
      if (p.startsWith('../data/seoul/')) {
        return p.replace('../data/seoul/', '/images/')
      }
      return p
    })
  }

  // Fall back to CDN URLs until scraper has downloaded them
  const naver = getMeta(cafe)
  if (naver) {
    return naver.thumUrls?.length ? naver.thumUrls : naver.thumUrl ? [naver.thumUrl] : []
  }
  const kakao = getKakaoMeta(cafe)
  if (kakao) {
    const urls = kakao.image_info?.image_main_urls
    if (urls?.length) return urls
    if (kakao.img) return [kakao.img]
  }
  const google = getGoogleMeta(cafe)
  if (google) {
    if (google.local_images?.length) return google.local_images
  }
  return []
}

export function isOpenNow(cafe: Cafe): boolean {
  const meta = getMeta(cafe)
  return meta?.businessStatus?.status?.code === 2
}

export function hasImage(cafe: Cafe): boolean {
  return getImages(cafe).length > 0
}

export function hasMultipleImages(cafe: Cafe): boolean {
  return getImages(cafe).length > 1
}

export function imageCount(cafes: Cafe[]): number {
  return cafes.filter(hasImage).length
}

export function multiImgCount(cafes: Cafe[]): number {
  return cafes.filter(hasMultipleImages).length
}
