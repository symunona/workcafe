export interface KakaoMetadata {
  img?: string
  image_info?: {
    image_main_urls?: string[]
    image_full_count?: number
  }
  reviewCount?: number
  tel?: string
  address?: string
  local_images?: string[]
}

export interface GoogleMetadata {
  name?: string
  lat?: number
  lon?: number
  url?: string
  provider_id?: string
  local_images?: string[]
}

export interface NaverMetadata {
  tel?: string
  category?: string[]
  businessStatus?: {
    status?: {
      code?: number      // 2 = open
      text?: string      // "영업 중"
      description?: string
    }
    businessHours?: string
  }
  reviewCount?: number
  placeReviewCount?: number
  thumUrl?: string
  thumUrls?: string[]
  roadAddress?: string
  shortAddress?: string[]
  local_images?: string[]
}

export interface Cafe {
  id: string
  provider: string
  provider_id: string
  name: string
  lat: number
  lon: number
  address: string
  url: string
  metadata: NaverMetadata | Record<string, unknown> | null
  scraped_at: string
}
