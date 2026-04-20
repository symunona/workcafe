import type { Cafe, NaverMetadata, KakaoMetadata, GoogleMetadata } from './types'

export const PROVIDER_COLORS: Record<string, string> = {
  naver:      '#22c55e', // green
  osm:        '#0ea5e9', // sky blue
  google:     '#ea4335', // google red
  kakao:      '#facc15', // yellow
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

export interface ImagePair {
  src: string
  fallback?: string  // original CDN URL if src is a local path
}

export function getImagePairs(cafe: Cafe): ImagePair[] {
  const naver = getMeta(cafe)
  if (naver) {
    const cdnUrls = naver.thumUrls?.length ? naver.thumUrls : naver.thumUrl ? [naver.thumUrl] : []
    if (naver.local_images?.length) {
      return naver.local_images.map((src, i) => ({ src, fallback: cdnUrls[i] }))
    }
    return cdnUrls.map(src => ({ src }))
  }
  const kakao = getKakaoMeta(cafe)
  if (kakao) {
    const cdnUrls = kakao.image_info?.image_main_urls?.length
      ? kakao.image_info.image_main_urls
      : kakao.img ? [kakao.img] : []
    if (kakao.local_images?.length) {
      return kakao.local_images.map((src, i) => ({ src, fallback: cdnUrls[i] }))
    }
    return cdnUrls.map(src => ({ src }))
  }
  const google = getGoogleMeta(cafe)
  if (google?.local_images?.length) {
    return google.local_images.map(src => ({ src }))
  }
  const anyMeta = cafe.metadata as { local_images?: string[], local_image_paths?: string[] } | null
  if (anyMeta?.local_images?.length) return anyMeta.local_images.map(src => ({ src }))
  if (anyMeta?.local_image_paths?.length) {
    return anyMeta.local_image_paths.map(p => ({
      src: p.startsWith('../data/seoul/') ? p.replace('../data/seoul/', '/images/') : p
    }))
  }
  return []
}

export function getImages(cafe: Cafe): string[] {
  return getImagePairs(cafe).map(p => p.src)
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
