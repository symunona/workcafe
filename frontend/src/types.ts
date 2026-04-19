export interface KakaoMetadata {
  img?: string
  image_info?: {
    image_main_urls?: string[]
    image_full_count?: number
  }
  photo_counts?: { total?: number }
  scraped_photos?: number
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
  scraped_photos?: number
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

export interface ImageInfo {
  cafe_id: string
  provider: string
  local_path: string
  image_url: string
  photo_id: string
  width: number
  height: number
  file_size: number
  scraped_at: string
}

export interface SourceCafe {
  id: string
  provider: string
  name: string
  lat: number
  lon: number
  address: string
  url: string
  metadata: Record<string, unknown> | null
  scraped_at: string
  images: ImageInfo[]
}

export interface CleanCafe {
  id: string
  name: string
  english_name?: string
  lat: number
  lon: number
  providers: string[]
  source_ids: string[]
  address: string
  url: string
  chain_name?: string
  chain_name_english?: string
  image_count: number
  // detail view only
  sources?: SourceCafe[]
  all_images?: ImageInfo[]
}
