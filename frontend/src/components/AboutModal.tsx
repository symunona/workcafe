declare const __GIT_SHA__: string
declare const __BUILD_DATE__: string

const MERMAID_DEF = `flowchart TB
    subgraph SC[" Scrapers "]
        N[Naver]
        K[Kakao]
        G[Google]
        O[OSM]
    end
    subgraph DL[" Image Downloaders "]
        I1[Naver]
        I2[Kakao]
        I3[Google]
    end
    N & K & G & O --> DB[("scraped.db<br/>~42k cafes")]
    DB --> NM["Normalize &<br/>Deduplicate"]
    DB --> I1 & I2 & I3
    I1 & I2 & I3 -.-> DB
    I1 & I2 & I3 --> FS[("📁 Images<br/>on disk")]
    FS --> AI["AI Taggers<br/>RAM · CLIP · YOLO"]
    NM --> CDB[("clean.db<br/>~30k cafes")]
    AI --> CDB
    CDB --> API["Go API<br/>:13854"]
    FS --> API
    API --> FE["React<br/>Map UI"]

    classDef scraper fill:#22c55e,stroke:#15803d,color:#fff
    classDef dl fill:#facc15,stroke:#ca8a04,color:#333
    classDef db fill:#3b82f6,stroke:#1d4ed8,color:#fff
    classDef fs fill:#0ea5e9,stroke:#0369a1,color:#fff
    classDef proc fill:#f97316,stroke:#c2410c,color:#fff
    classDef api fill:#8b5cf6,stroke:#6d28d9,color:#fff
    classDef ui fill:#ec4899,stroke:#be185d,color:#fff

    class N,K,G,O scraper
    class I1,I2,I3 dl
    class DB,CDB db
    class FS fs
    class NM,AI proc
    class API api
    class FE ui`

const MERMAID_URL = `https://mermaid.ink/img/${btoa(unescape(encodeURIComponent(MERMAID_DEF)))}`

interface Props {
  onClose: () => void
}

export function AboutModal({ onClose }: Props) {
  const buildDate = new Date(__BUILD_DATE__).toLocaleString()

  return (
    <div className="fixed inset-0 z-[1200] flex items-center justify-center p-4 bg-black/40" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-2xl max-w-2xl w-full p-6 flex flex-col gap-4 max-h-[90vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2">
            <img src="/favicon.svg" alt="" className="h-8 w-8" />
            <h2 className="text-xl font-bold text-gray-800">Workcafe Korea</h2>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl w-8 h-8 flex items-center justify-center">✕</button>
        </div>

        <p className="text-sm text-gray-600 leading-relaxed">
          A map of cafes in Seoul aggregated from Naver Maps, Kakao Maps, and Google Maps.
          Scrapes ~42k venues, deduplicates into ~30k clean entries, tags images with AI
          (RAM, CLIP, YOLO), and lets you filter by images, tags, chains, and providers.
        </p>

        {/* Data pipeline diagram */}
        <div>
          <div className="text-xs text-gray-400 font-semibold uppercase tracking-wider mb-2">Data Pipeline</div>
          <div className="bg-gray-50 rounded-xl overflow-hidden">
            <img
              src={MERMAID_URL}
              alt="Data pipeline diagram"
              className="w-full"
              loading="lazy"
            />
          </div>
        </div>

        <a
          href="https://github.com/symunona/workcafe"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-3 px-4 py-3 bg-gray-900 text-white rounded-xl hover:bg-gray-700 transition-colors text-sm font-medium"
        >
          <svg height="20" viewBox="0 0 16 16" width="20" fill="currentColor">
            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/>
          </svg>
          github.com/symunona/workcafe
        </a>

        <div className="border-t border-gray-100 pt-3 flex flex-col gap-1 text-[11px] text-gray-400 font-mono">
          <span>Build: {__GIT_SHA__}</span>
          <span>Date: {buildDate}</span>
        </div>
      </div>
    </div>
  )
}
