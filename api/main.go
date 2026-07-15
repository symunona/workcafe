package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"math"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

type Cafe struct {
	ID         string          `json:"id"`
	Provider   string          `json:"provider"`
	ProviderID string          `json:"provider_id"`
	Name       string          `json:"name"`
	Lat        float64         `json:"lat"`
	Lon        float64         `json:"lon"`
	Address    string          `json:"address"`
	URL        string          `json:"url"`
	Metadata   json.RawMessage `json:"metadata"`
	ScrapedAt  string          `json:"scraped_at"`
}

type ServiceStatus struct {
	Name       string `json:"name"`
	Unit       string `json:"unit"`
	State      string `json:"state"`
	Active     bool   `json:"active"`
	ExitStatus string `json:"exit_status,omitempty"` // "success", "killed", "failed", or ""
	LastLog    string `json:"last_log,omitempty"`
}

type ProviderMetrics struct {
	Provider            string  `json:"provider"`
	CafesLastHour       int     `json:"cafes_last_hour"`
	Cafes24h            int     `json:"cafes_24h"`
	ImagesLastHour      int     `json:"images_last_hour"`
	Images24h           int     `json:"images_24h"`
	DownloadedLastHour  int     `json:"downloaded_last_hour"`
	Downloaded24h       int     `json:"downloaded_24h"`
	Total               int     `json:"total"`
	HasWebsite          int     `json:"has_website"`
	// Image coverage distribution
	CafesWithImages int     `json:"cafes_with_images"`
	Cafes2Plus      int     `json:"cafes_2plus"`
	Cafes10Plus     int     `json:"cafes_10plus"`
	Cafes50Plus     int     `json:"cafes_50plus"`
	AvgImages       float64 `json:"avg_images"`
	TotalImages     int     `json:"total_images"`
}

type DiskStats struct {
	DataDirGB    float64 `json:"data_dir_gb"`
	FolderSizeGB float64 `json:"folder_size_gb"`
	LimitGB      float64 `json:"limit_gb"`
	UsedPct      float64 `json:"used_pct"`
	FreeGB       float64 `json:"free_gb"`
}

type QueueEntry struct {
	QueueDepth int    `json:"queue_depth"`
	UpdatedAt  string `json:"updated_at"`
}

type HourlyStat struct {
	Hour   string `json:"hour"`
	Cafes  int    `json:"scraped_cafes"`
	Images int    `json:"images"`
	Provider string `json:"provider"`
}


type StatusResponse struct {
	Services          []ServiceStatus        `json:"services"`
	PerProvider       []ProviderMetrics      `json:"per_provider"`
	FinishedProviders []string               `json:"finished_providers"`
	OverallTaggedImages    int                    `json:"overall_tagged_images"`
	OverallImgsPerHour     float64                `json:"overall_imgs_per_hour"`
	TotalCafes             int                    `json:"total_cafes"`
	TotalImages            int                    `json:"total_images"`
	// Pipeline funnel: raw scrape → merge → image download → tag.
	// Image stages all read clean.db (the store the tagger + frontend use), so the
	// total ≥ downloaded ≥ processed invariant holds (scraped.db is trimmed/deduped).
	MergeQueue             int                    `json:"funnel_merge_queue"`       // raw cafes not yet merged
	MergedCafes            int                    `json:"funnel_merged_cafes"`      // dedup'd clean_cafes
	ImagesTotal            int                    `json:"funnel_images_total"`      // image rows in clean.db
	ImagesDownloaded       int                    `json:"funnel_images_downloaded"` // clean.db images with bytes on disk
	CafesLastHour          int                    `json:"cafes_last_hour"`
	Cafes24h               int                    `json:"cafes_24h"`
	ImagesLastHour         int                    `json:"images_last_hour"`
	Images24h              int                    `json:"images_24h"`
	DownloadedLastHour     int                    `json:"downloaded_last_hour"`
	Downloaded24h          int                    `json:"downloaded_24h"`
	LastCafeAt             string                 `json:"last_cafe_at"`
	LastImageAt            string                 `json:"last_image_at"`
	MBPerDay               float64                `json:"mb_per_day"`
	Disk                   DiskStats              `json:"disk"`
	DbQueue                map[string]QueueEntry  `json:"db_queue"`
	HourlyStats            []HourlyStat           `json:"hourly_stats"`
}

// patchLocalImages injects confirmed local_paths from the images table into metadata.
func patchLocalImages(meta, imgPathsJSON string) json.RawMessage {
	var m map[string]json.RawMessage
	if err := json.Unmarshal([]byte(meta), &m); err != nil {
		m = map[string]json.RawMessage{}
	}
	m["local_images"] = json.RawMessage(imgPathsJSON)
	out, _ := json.Marshal(m)
	return json.RawMessage(out)
}

var serviceMap = map[string]string{
	"db-server":     "workcafe-db-server",
	"api":           "workcafe-api",
	"frontend":      "workcafe-frontend",
	"kakao":         "workcafe-scraper-kakao",
	"google":        "workcafe-scraper-google",
	"osm":           "workcafe-scraper-osm",
	"naver":         "workcafe-scraper-naver",
	"kakao-images":    "workcafe-kakao-images",
	"naver-images":    "workcafe-naver-images",
	"google-images":   "workcafe-google-images",
	"kakao-metadata":  "workcafe-kakao-metadata",
	"naver-metadata":  "workcafe-naver-metadata",
}

var serviceOrder = []string{"db-server", "api", "frontend", "kakao", "google", "osm", "naver", "kakao-images", "naver-images", "google-images", "kakao-metadata", "naver-metadata"}

var imageScraperNames = map[string]bool{
	"kakao-images":  true,
	"naver-images":  true,
	"google-images": true,
}

// syncWatchdog enables the watchdog timer if any image scraper is active, disables otherwise.
func syncWatchdog() {
	anyActive := false
	for name := range imageScraperNames {
		unit := serviceMap[name]
		_, active := getServiceState(unit)
		if active {
			anyActive = true
			break
		}
	}
	action := "disable"
	if anyActive {
		action = "enable"
	}
	exec.Command("systemctl", "--user", action, "--now", "workcafe-watchdog.timer").Run()
}

func getServiceState(unit string) (string, bool) {
	out, err := exec.Command("systemctl", "--user", "is-active", unit).Output()
	state := strings.TrimSpace(string(out))
	if err != nil || state == "" {
		state = "inactive"
	}
	return state, state == "active"
}

// getServiceExitStatus returns "success", "killed", "failed", or "" for active/unknown.
func getServiceExitStatus(unit string) string {
	out, err := exec.Command("systemctl", "--user", "show", unit,
		"--property=Result,ExecMainStatus").Output()
	if err != nil {
		return ""
	}
	props := map[string]string{}
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if k, v, ok := strings.Cut(line, "="); ok {
			props[k] = v
		}
	}
	result := props["Result"]
	exitCode := props["ExecMainStatus"]
	if result == "success" && exitCode == "0" {
		return "success"
	}
	if exitCode == "15" || result == "signal" {
		return "killed"
	}
	if result == "exit-code" || result == "failed" {
		return "failed"
	}
	return ""
}

// getServiceLastLog returns the last meaningful log line for a service.
// For inactive/failed services this explains why it stopped.
func getServiceLastLog(unit string) string {
	out, err := exec.Command("journalctl", "--user", "-u", unit, "--no-pager", "-n", "5",
		"--output=short-monotonic").Output()
	if err != nil || len(out) == 0 {
		return ""
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	// Walk from newest to oldest, skip systemd bookkeeping lines, return first real app line
	for i := len(lines) - 1; i >= 0; i-- {
		l := lines[i]
		if strings.Contains(l, "systemd[") {
			continue
		}
		// Strip the monotonic timestamp prefix (e.g. "[  123.456] hostname unit: ")
		// journalctl short-monotonic format: "[timestamp] host unit[pid]: message"
		if idx := strings.Index(l, "]: "); idx != -1 {
			l = strings.TrimSpace(l[idx+3:])
		}
		// Remove the "unit[pid]: " prefix that sometimes follows
		if idx := strings.Index(l, "]: "); idx != -1 {
			l = strings.TrimSpace(l[idx+3:])
		}
		if l != "" {
			return l
		}
	}
	return ""
}

// Disk stats cache — recompute at most once every 5 minutes
var (
	diskCacheMu  sync.Mutex
	diskCached   DiskStats
	diskCachedAt time.Time
)

// Service status cache — recompute at most once every 15 seconds
var (
	svcCacheMu  sync.Mutex
	svcCached   []ServiceStatus
	svcCachedAt time.Time
)

func getCachedServices() []ServiceStatus {
	const ttl = 15 * time.Second
	svcCacheMu.Lock()
	defer svcCacheMu.Unlock()
	if time.Since(svcCachedAt) < ttl {
		return svcCached
	}
	services := make([]ServiceStatus, len(serviceOrder))
	var wg sync.WaitGroup
	for i, name := range serviceOrder {
		wg.Add(1)
		go func(i int, name string) {
			defer wg.Done()
			unit := serviceMap[name]
			state, active := getServiceState(unit)
			svc := ServiceStatus{Name: name, Unit: unit, State: state, Active: active}
			if !active {
				svc.ExitStatus = getServiceExitStatus(unit)
			}
			svc.LastLog = getServiceLastLog(unit)
			services[i] = svc
		}(i, name)
	}
	wg.Wait()
	svcCached = services
	svcCachedAt = time.Now()
	return services
}


// ─── Split /api/stats/* section responses ───────────────────────────────────────
// The monolithic /api/status computes every section serially, so the slowest
// scans on the 5GB clean.db (funnel images-downloaded ~20-30s, tagger
// COUNT(DISTINCT) ~8-14s) block the fast summary from ever rendering. These
// endpoints expose each logical section on its own so the frontend can fan out
// and paint progressively (fastest-first). Each response carries ONLY its own
// keys — a subset of StatusResponse's JSON shape — so the client can shallow-merge
// them into one status object without a section clobbering its siblings.
type StatsOverview struct {
	Services           []ServiceStatus       `json:"services"`
	TotalCafes         int                   `json:"total_cafes"`
	TotalImages        int                   `json:"total_images"`
	CafesLastHour      int                   `json:"cafes_last_hour"`
	Cafes24h           int                   `json:"cafes_24h"`
	ImagesLastHour     int                   `json:"images_last_hour"`
	Images24h          int                   `json:"images_24h"`
	DownloadedLastHour int                   `json:"downloaded_last_hour"`
	Downloaded24h      int                   `json:"downloaded_24h"`
	MBPerDay           float64               `json:"mb_per_day"`
	LastCafeAt         string                `json:"last_cafe_at"`
	LastImageAt        string                `json:"last_image_at"`
	Disk               DiskStats             `json:"disk"`
	DbQueue            map[string]QueueEntry `json:"db_queue"`
}

// ProviderCore = the "Scraping Metrics" table columns (cafe + image throughput).
type ProviderCore struct {
	Provider           string `json:"provider"`
	Total              int    `json:"total"`
	HasWebsite         int    `json:"has_website"`
	CafesLastHour      int    `json:"cafes_last_hour"`
	Cafes24h           int    `json:"cafes_24h"`
	ImagesLastHour     int    `json:"images_last_hour"`
	Images24h          int    `json:"images_24h"`
	DownloadedLastHour int    `json:"downloaded_last_hour"`
	Downloaded24h      int    `json:"downloaded_24h"`
}
type StatsProviders struct {
	PerProvider []ProviderCore `json:"per_provider"`
}

// ProviderCoverage = the "Image Coverage" table columns. Distinct key set from
// ProviderCore (except provider) so the client merges the two per_provider
// payloads by provider without overwriting.
type ProviderCoverage struct {
	Provider        string  `json:"provider"`
	CafesWithImages int     `json:"cafes_with_images"`
	Cafes2Plus      int     `json:"cafes_2plus"`
	Cafes10Plus     int     `json:"cafes_10plus"`
	Cafes50Plus     int     `json:"cafes_50plus"`
	AvgImages       float64 `json:"avg_images"`
	TotalImages     int     `json:"total_images"`
}
type StatsCoverage struct {
	PerProvider []ProviderCoverage `json:"per_provider"`
}

type StatsHourly struct {
	HourlyStats []HourlyStat `json:"hourly_stats"`
}

type StatsTagging struct {
	OverallTaggedImages int     `json:"overall_tagged_images"`
	OverallImgsPerHour  float64 `json:"overall_imgs_per_hour"`
}

type StatsFunnel struct {
	MergeQueue       int `json:"funnel_merge_queue"`
	MergedCafes      int `json:"funnel_merged_cafes"`
	ImagesTotal      int `json:"funnel_images_total"`
	ImagesDownloaded int `json:"funnel_images_downloaded"`
}

// sectionCache serves one section's marshaled JSON, recomputing at most once per
// TTL. The lock is held across compute so concurrent callers coalesce onto a
// single computation (no stampede on the multi-second clean.db scans), mirroring
// the /api/status full-response cache.
type sectionCache struct {
	mu   sync.Mutex
	body []byte
	at   time.Time
}

func (c *sectionCache) serve(w http.ResponseWriter, ttl time.Duration, compute func() (interface{}, error)) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if time.Since(c.at) < ttl && c.body != nil {
		w.Write(c.body)
		return
	}
	v, err := compute()
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	body, err := json.Marshal(v)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	c.body = body
	c.at = time.Now()
	w.Write(body)
}

var (
	statsOverviewCache  sectionCache
	statsProvidersCache sectionCache
	statsCoverageCache  sectionCache
	statsHourlyCache    sectionCache
	statsTaggingCache   sectionCache
	statsFunnelCache    sectionCache
)

// ─── Scrape-coverage grid ──────────────────────────────────────────────────────
// Mirrors scraper/lib/utils.py: STEP_SIZE 0.01°, origin = Seoul City Hall.
const (
	coverageStep      = 0.01
	coverageCenterLat = 37.490230
	coverageCenterLon = 126.994312
)

type coverageRegion struct {
	lat, lon float64
	radKm    int
}

// Known regions (data/regions.json). radKm ≈ grid cells (each ~1km).
var coverageRegions = map[string]coverageRegion{
	"seoul":    {37.490230, 126.994312, 20},
	"busan":    {35.10066, 129.03185, 20},
	"haeundae": {35.15977, 129.15889, 10},
}

// round mirrors Python's round-half-to-even-ish banker's rounding closely enough
// for grid binning; Go's math.Round is round-half-away-from-zero which matches the
// scraper's get_grid_coords (Python round() is banker's, but cells differ only at
// exact .5 boundaries which scraped lat/lon never hit). Use math.Round.
func round(f float64) float64 { return math.Round(f) }

// CoverageProvider is one provider's coverage in a cell.
type CoverageProvider struct {
	Status string `json:"status,omitempty"` // "completed" if any progress row present
	Cafes  int    `json:"cafes"`
}

// CoverageCell is one 1km grid cell.
type CoverageCell struct {
	GridX      int                          `json:"grid_x"`
	GridY      int                          `json:"grid_y"`
	BBox       [4]float64                   `json:"bbox"` // [minLat,minLon,maxLat,maxLon]
	Providers  map[string]*CoverageProvider `json:"providers"`
	TotalCafes int                          `json:"total_cafes"`
}

// coverageBaseProvider maps a progress.provider (e.g. "kakao_CE7", "google_카페")
// to its base provider name; returns "" for sentinel/unknown rows.
func coverageBaseProvider(p string) string {
	switch {
	case strings.HasPrefix(p, "kakao"):
		return "kakao"
	case strings.HasPrefix(p, "google"):
		return "google"
	case strings.HasPrefix(p, "naver"):
		return "naver"
	case strings.HasPrefix(p, "osm"):
		return "osm"
	}
	return ""
}

// Scrape-coverage cache — full response per (region/bbox) bounds, ~8s TTL like /api/status.
var (
	coverageCacheMu sync.Mutex
	coverageCache   = map[string][]byte{}
	coverageCacheAt = map[string]time.Time{}
)

const coverageCacheTTL = 8 * time.Second

// getScrapeCoverage builds (or serves cached) the coverage payload for the given
// grid bounds. Bounds of MinInt/MaxInt mean "no limit".
func getScrapeCoverage(db *sql.DB, minGX, maxGX, minGY, maxGY int) []byte {
	key := fmt.Sprintf("%d:%d:%d:%d", minGX, maxGX, minGY, maxGY)
	coverageCacheMu.Lock()
	defer coverageCacheMu.Unlock()
	if t, ok := coverageCacheAt[key]; ok && time.Since(t) < coverageCacheTTL {
		return coverageCache[key]
	}

	inBounds := func(gx, gy int) bool {
		return gx >= minGX && gx <= maxGX && gy >= minGY && gy <= maxGY
	}

	cells := map[[2]int]*CoverageCell{}
	getCell := func(gx, gy int) *CoverageCell {
		k := [2]int{gx, gy}
		c, ok := cells[k]
		if !ok {
			c = &CoverageCell{
				GridX: gx, GridY: gy,
				BBox: [4]float64{
					coverageCenterLat + (float64(gy)-0.5)*coverageStep,
					coverageCenterLon + (float64(gx)-0.5)*coverageStep,
					coverageCenterLat + (float64(gy)+0.5)*coverageStep,
					coverageCenterLon + (float64(gx)+0.5)*coverageStep,
				},
				Providers: map[string]*CoverageProvider{},
			}
			cells[k] = c
		}
		return c
	}

	// 1) progress rows → which cells each provider finished.
	//    Skip the (9999,9999) "*_finished" sentinel rows.
	progRows, err := db.Query(`SELECT grid_x, grid_y, provider FROM progress WHERE NOT (grid_x = 9999 AND grid_y = 9999)`)
	if err == nil {
		defer progRows.Close()
		for progRows.Next() {
			var gx, gy int
			var prov string
			if progRows.Scan(&gx, &gy, &prov) != nil {
				continue
			}
			base := coverageBaseProvider(prov)
			if base == "" || !inBounds(gx, gy) {
				continue
			}
			c := getCell(gx, gy)
			cp, ok := c.Providers[base]
			if !ok {
				cp = &CoverageProvider{}
				c.Providers[base] = cp
			}
			cp.Status = "completed"
		}
	}

	// 2) scraped_cafes binned to cells → per-provider cafe counts.
	//    Bin in SQL: cast(round((lon-origin)/step)) — sqlite ROUND is half-away-from-zero,
	//    matching Go math.Round and the scraper's get_grid_coords.
	cafeRows, err := db.Query(`
		SELECT CAST(ROUND((lon - ?) / ?) AS INTEGER) AS gx,
		       CAST(ROUND((lat - ?) / ?) AS INTEGER) AS gy,
		       provider, COUNT(*)
		FROM scraped_cafes
		WHERE lat IS NOT NULL AND lon IS NOT NULL
		GROUP BY gx, gy, provider`,
		coverageCenterLon, coverageStep, coverageCenterLat, coverageStep)
	if err == nil {
		defer cafeRows.Close()
		for cafeRows.Next() {
			var gx, gy, n int
			var prov string
			if cafeRows.Scan(&gx, &gy, &prov, &n) != nil {
				continue
			}
			base := coverageBaseProvider(prov)
			if base == "" {
				base = prov
			}
			if !inBounds(gx, gy) {
				continue
			}
			c := getCell(gx, gy)
			cp, ok := c.Providers[base]
			if !ok {
				cp = &CoverageProvider{}
				c.Providers[base] = cp
			}
			cp.Cafes += n
			c.TotalCafes += n
		}
	}

	// Flatten + per-provider rollup totals.
	out := make([]CoverageCell, 0, len(cells))
	rollup := map[string]*CoverageProvider{}
	totalCafes := 0
	for _, c := range cells {
		out = append(out, *c)
		totalCafes += c.TotalCafes
		for base, cp := range c.Providers {
			r, ok := rollup[base]
			if !ok {
				r = &CoverageProvider{}
				rollup[base] = r
			}
			r.Cafes += cp.Cafes
		}
	}
	// Cells-completed-per-provider rollup (count of completed cells).
	rollupCells := map[string]int{}
	for _, c := range cells {
		for base, cp := range c.Providers {
			if cp.Status == "completed" {
				rollupCells[base]++
			}
		}
	}

	sort.Slice(out, func(i, j int) bool {
		if out[i].GridY != out[j].GridY {
			return out[i].GridY < out[j].GridY
		}
		return out[i].GridX < out[j].GridX
	})

	type regionRollup struct {
		Provider      string `json:"provider"`
		CafeCount     int    `json:"cafes"`
		CellsComplete int    `json:"cells_complete"`
	}
	rollupList := make([]regionRollup, 0, len(rollup))
	for base, r := range rollup {
		rollupList = append(rollupList, regionRollup{Provider: base, CafeCount: r.Cafes, CellsComplete: rollupCells[base]})
	}
	sort.Slice(rollupList, func(i, j int) bool { return rollupList[i].CafeCount > rollupList[j].CafeCount })

	resp := map[string]interface{}{
		"cells":            out,
		"cell_count":       len(out),
		"total_cafes":      totalCafes,
		"per_provider":     rollupList,
		"cell_size_deg":    coverageStep,
		"origin":           [2]float64{coverageCenterLat, coverageCenterLon},
	}
	bodyBytes, _ := json.Marshal(resp)
	coverageCache[key] = bodyBytes
	coverageCacheAt[key] = time.Now()
	return bodyBytes
}


func getDiskStats(dataDir string) DiskStats {
	const ttl = 5 * time.Minute

	diskCacheMu.Lock()
	if time.Since(diskCachedAt) < ttl {
		result := diskCached
		diskCacheMu.Unlock()
		return result
	}
	stale := diskCached
	diskCacheMu.Unlock()

	// Return stale cache immediately; refresh in background so WalkDir never blocks a request.
	go func() {
		var stat syscall.Statfs_t
		if err := syscall.Statfs(dataDir, &stat); err != nil {
			return
		}

		freeBytes := float64(stat.Bavail) * float64(stat.Bsize)
		totalBytes := float64(stat.Blocks) * float64(stat.Bsize)
		usedBytes := totalBytes - float64(stat.Bfree)*float64(stat.Bsize)

		freeGB := freeBytes / (1024 * 1024 * 1024)
		totalGB := totalBytes / (1024 * 1024 * 1024)
		usedGB := usedBytes / (1024 * 1024 * 1024)

		var usedPct float64
		if totalGB > 0 {
			usedPct = (usedGB / totalGB) * 100
		}

		var folderSize int64
		filepath.WalkDir(dataDir, func(path string, d os.DirEntry, err error) error {
			if err != nil {
				return nil
			}
			if !d.IsDir() {
				info, err := d.Info()
				if err == nil {
					folderSize += info.Size()
				}
			}
			return nil
		})
		folderSizeGB := float64(folderSize) / (1024 * 1024 * 1024)

		diskCacheMu.Lock()
		diskCached = DiskStats{
			DataDirGB:    math_round2(usedGB),
			FolderSizeGB: math_round2(folderSizeGB),
			LimitGB:      math_round2(totalGB),
			UsedPct:      math_round2(usedPct),
			FreeGB:       math_round2(freeGB),
		}
		diskCachedAt = time.Now()
		diskCacheMu.Unlock()
	}()

	return stale
}

func math_round2(f float64) float64 {
	return float64(int(f*100)) / 100
}

func getQueueStats(dataDir string) map[string]QueueEntry {
	statsPath := filepath.Join(dataDir, "scraper_queue_stats.json")
	data, err := os.ReadFile(statsPath)
	if err != nil {
		return map[string]QueueEntry{}
	}
	result := map[string]QueueEntry{}
	json.Unmarshal(data, &result)
	return result
}

func corsJSON(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
}

// ─── Snapshot DB cache ────────────────────────────────────────────────────────

type snapshotCache struct {
	mu      sync.Mutex
	dbs     map[string]*sql.DB
	dataDir string
}

func newSnapshotCache(dataDir string) *snapshotCache {
	return &snapshotCache{dbs: make(map[string]*sql.DB), dataDir: dataDir}
}

func (sc *snapshotCache) get(name string) (*sql.DB, error) {
	sc.mu.Lock()
	defer sc.mu.Unlock()
	if db, ok := sc.dbs[name]; ok {
		return db, nil
	}
	path := filepath.Join(sc.dataDir, "history", "clean_"+name+".db")
	if _, err := os.Stat(path); err != nil {
		return nil, fmt.Errorf("snapshot not found: %s", name)
	}
	db, err := sql.Open("sqlite3", "file:"+path+"?_busy_timeout=5000")
	if err != nil {
		return nil, err
	}
	// Idempotent schema migrations for snapshots created before these schema versions.
	db.Exec(`ALTER TABLE image_tags ADD COLUMN boxes TEXT`)
	db.Exec(`ALTER TABLE scraped_cafes ADD COLUMN scraped_at TEXT`)
	db.Exec(`ALTER TABLE images ADD COLUMN width INTEGER`)
	db.Exec(`ALTER TABLE images ADD COLUMN height INTEGER`)
	db.Exec(`ALTER TABLE images ADD COLUMN scraped_at TEXT`)
	db.Exec(`ALTER TABLE images ADD COLUMN tagged_at TEXT`)
	db.Exec(`CREATE TABLE IF NOT EXISTS cafe_chains (id TEXT PRIMARY KEY, name TEXT, name_english TEXT, count INTEGER)`)
	db.SetMaxOpenConns(4)
	sc.dbs[name] = db
	return db, nil
}

// dbForRequest returns the snapshot DB if ?snapshot= is set, else the live db.
func (sc *snapshotCache) dbForRequest(r *http.Request, live *sql.DB) *sql.DB {
	name := r.URL.Query().Get("snapshot")
	if name == "" {
		return live
	}
	sdb, err := sc.get(name)
	if err != nil {
		return live
	}
	return sdb
}

type SnapshotInfo struct {
	Name      string `json:"name"`
	Date      string `json:"date"`
	CafeCount int    `json:"cafe_count"`
	Notes     string `json:"notes"`
}

func (sc *snapshotCache) list() ([]SnapshotInfo, error) {
	historyDir := filepath.Join(sc.dataDir, "history")
	entries, err := os.ReadDir(historyDir)
	if err != nil {
		if os.IsNotExist(err) {
			return []SnapshotInfo{}, nil
		}
		return nil, err
	}
	type snapshotEntry struct {
		info  SnapshotInfo
		mtime int64
	}
	var entries2 []snapshotEntry
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".db") {
			continue
		}
		// clean_2026-04-23-v1.db → name = 2026-04-23-v1
		name := strings.TrimPrefix(strings.TrimSuffix(e.Name(), ".db"), "clean_")
		date := ""
		if len(name) >= 10 {
			date = name[:10]
		}
		var mtime int64
		if fi, err := e.Info(); err == nil {
			mtime = fi.ModTime().Unix()
		}
		// read cafe count
		count := 0
		if sdb, err := sc.get(name); err == nil {
			sdb.QueryRow("SELECT COUNT(*) FROM clean_cafes").Scan(&count)
		}
		// read notes preview from .md
		notes := ""
		mdPath := filepath.Join(historyDir, "clean_"+name+".md")
		if mdBytes, err := os.ReadFile(mdPath); err == nil {
			notes = string(mdBytes)
		}
		entries2 = append(entries2, snapshotEntry{
			info:  SnapshotInfo{Name: name, Date: date, CafeCount: count, Notes: notes},
			mtime: mtime,
		})
	}
	// Sort newest first by file mtime
	sort.Slice(entries2, func(i, j int) bool { return entries2[i].mtime > entries2[j].mtime })
	out := make([]SnapshotInfo, len(entries2))
	for i, e := range entries2 {
		out[i] = e.info
	}
	return out, nil
}

func main() {
	dbPath := os.Getenv("DB_PATH")
	if dbPath == "" {
		dbPath = "../data/seoul/clean.db"
	}
	rawDbPath := os.Getenv("RAW_DB_PATH")
	if rawDbPath == "" {
		rawDbPath = "../data/seoul/scraped.db"
	}
	dataDir := os.Getenv("DATA_DIR")
	if dataDir == "" {
		dataDir = "../data/seoul"
	}

	// clean.db — normalized cafe + image data, served to frontend
	// _pragma=journal_mode(WAL): join existing WAL; _pragma=busy_timeout(5000): wait up to 5s on lock
	db, err := sql.Open("sqlite3", "file:"+dbPath+"?_journal_mode=WAL&_busy_timeout=5000&_synchronous=NORMAL")
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close()

	// scraped.db — live scraper output; used only for real-time metrics in /api/status
	rawDb, err := sql.Open("sqlite3", "file:"+rawDbPath+"?_journal_mode=WAL&_busy_timeout=5000&_synchronous=NORMAL")
	if err != nil {
		log.Fatal(err)
	}
	defer rawDb.Close()

	// image_tags may not exist in clean.db (tags live in experiment snapshots).
	// Create empty table so image queries don't crash when no snapshot selected.
	db.Exec(`CREATE TABLE IF NOT EXISTS image_tags (
		image_id INTEGER NOT NULL,
		tag      TEXT    NOT NULL,
		score    REAL    NOT NULL DEFAULT 1.0,
		boxes    TEXT,
		PRIMARY KEY (image_id, tag)
	)`)
	// Idempotent: add columns to DBs created before these schema versions.
	db.Exec(`ALTER TABLE image_tags ADD COLUMN boxes TEXT`)
	db.Exec(`ALTER TABLE image_tags ADD COLUMN tagged_at TEXT`)
	db.Exec(`ALTER TABLE image_tags ADD COLUMN tagger TEXT`)
	// Indexes for tagger stats queries (cover GROUP BY tagger, COUNT(DISTINCT image_id), tagged_at range).
	db.Exec(`CREATE INDEX IF NOT EXISTS idx_image_tags_tagger        ON image_tags(tagger)`)
	db.Exec(`CREATE INDEX IF NOT EXISTS idx_image_tags_tagged_at     ON image_tags(tagged_at)`)
	db.Exec(`CREATE INDEX IF NOT EXISTS idx_image_tags_tagger_image  ON image_tags(tagger, image_id)`)

	snapshots := newSnapshotCache(dataDir)

	mux := http.NewServeMux()

	mux.Handle("/images/", http.StripPrefix("/images/", http.FileServer(http.Dir(dataDir))))

	// ── GET /api/snapshots ────────────────────────────────────────────────────
	mux.HandleFunc("/api/snapshots", func(w http.ResponseWriter, r *http.Request) {
		corsJSON(w)
		list, err := snapshots.list()
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		json.NewEncoder(w).Encode(list)
	})

	// ── GET /api/cafe?id=... ─────────────────────────────────────────────────
	mux.HandleFunc("/api/cafe", func(w http.ResponseWriter, r *http.Request) {
		id := r.URL.Query().Get("id")
		if id == "" {
			http.Error(w, "id required", 400)
			return
		}
		row := db.QueryRow(`
			SELECT c.id, COALESCE(c.provider,''), COALESCE(c.provider_id,''), COALESCE(c.name,''),
			       c.lat, c.lon, COALESCE(c.address,''), COALESCE(c.url,''),
			       COALESCE(c.metadata,'null'), COALESCE(c.scraped_at,''),
			       COALESCE(img_agg.paths,'[]')
			FROM scraped_cafes c
			LEFT JOIN (
			    SELECT cafe_id, json_group_array(local_path) as paths
			    FROM images WHERE file_size > 0 GROUP BY cafe_id
			) img_agg ON img_agg.cafe_id = c.id
			WHERE c.id = ?`, id)
		var c Cafe
		var meta, imgPaths string
		if err := row.Scan(&c.ID, &c.Provider, &c.ProviderID, &c.Name, &c.Lat, &c.Lon, &c.Address, &c.URL, &meta, &c.ScrapedAt, &imgPaths); err != nil {
			http.Error(w, "not found", 404)
			return
		}
		c.Metadata = patchLocalImages(meta, imgPaths)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(c)
	})

	// ── GET /api/scraped_cafes ────────────────────────────────────────────────────────
	mux.HandleFunc("/api/scraped_cafes", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()

		const limit = 1000
		var conditions []string
		var args []interface{}

		if minLat, maxLat, minLon, maxLon := q.Get("minLat"), q.Get("maxLat"), q.Get("minLon"), q.Get("maxLon"); minLat != "" && maxLat != "" && minLon != "" && maxLon != "" {
			conditions = append(conditions, "lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?")
			args = append(args, minLat, maxLat, minLon, maxLon)
		}

		if q.Get("multipleImages") == "true" {
			conditions = append(conditions, "(SELECT COUNT(*) FROM images WHERE cafe_id = c.id AND file_size > 0) >= 2")
		} else if q.Get("withImages") == "true" {
			conditions = append(conditions, "(SELECT COUNT(*) FROM images WHERE cafe_id = c.id AND file_size > 0) >= 1")
		}

		if providers := q.Get("providers"); providers != "" {
			provList := strings.Split(providers, ",")
			placeholders := make([]string, len(provList))
			for i, p := range provList {
				placeholders[i] = "?"
				args = append(args, p)
			}
			conditions = append(conditions, "provider IN ("+strings.Join(placeholders, ",")+")")
		}

		if maxScrapeDate := q.Get("maxScrapeDate"); maxScrapeDate != "" {
			if ts, err := strconv.ParseInt(maxScrapeDate, 10, 64); err == nil {
				dt := time.Unix(ts/1000, 0).UTC().Format("2006-01-02 15:04:05")
				conditions = append(conditions, "scraped_at <= ?")
				args = append(args, dt)
			}
		}

		if q.Get("openNow") == "true" {
			conditions = append(conditions, "json_extract(metadata, '$.businessStatus.status.code') = 2")
		}

		whereClause := ""
		if len(conditions) > 0 {
			whereClause = "WHERE " + strings.Join(conditions, " AND ")
		}

		var totalRows int
		countArgs := make([]interface{}, len(args))
		copy(countArgs, args)
		db.QueryRow("SELECT COUNT(*) FROM scraped_cafes "+whereClause, countArgs...).Scan(&totalRows)

		dataArgs := append(args, limit)
		rows, err := db.Query(`
			SELECT c.id, c.provider, c.provider_id, c.name, c.lat, c.lon,
			       COALESCE(c.address,''), COALESCE(c.url,''),
			       COALESCE(c.metadata,'null'), COALESCE(c.scraped_at,''),
			       COALESCE(img_agg.paths,'[]')
			FROM scraped_cafes c
			LEFT JOIN (
			    SELECT cafe_id, json_group_array(local_path) as paths
			    FROM images WHERE file_size > 0 GROUP BY cafe_id
			) img_agg ON img_agg.cafe_id = c.id
			`+whereClause+` ORDER BY RANDOM() LIMIT ?`, dataArgs...)
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		defer rows.Close()

		scraped_cafes := make([]Cafe, 0, limit)
		for rows.Next() {
			var c Cafe
			var meta, imgPaths string
			if err := rows.Scan(&c.ID, &c.Provider, &c.ProviderID, &c.Name, &c.Lat, &c.Lon, &c.Address, &c.URL, &meta, &c.ScrapedAt, &imgPaths); err != nil {
				http.Error(w, err.Error(), 500)
				return
			}
			c.Metadata = patchLocalImages(meta, imgPaths)
			scraped_cafes = append(scraped_cafes, c)
		}

		corsJSON(w)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"scraped_cafes":   scraped_cafes,
			"showing": len(scraped_cafes),
			"total":   totalRows,
		})
	})

	// ── GET /api/filter-stats ─────────────────────────────────────────────────
	mux.HandleFunc("/api/filter-stats", func(w http.ResponseWriter, r *http.Request) {
		type ProviderCount struct {
			Name  string `json:"name"`
			Count int    `json:"count"`
		}
		type FilterStats struct {
			Total          int             `json:"total"`
			WithImages     int             `json:"with_images"`
			MultipleImages int             `json:"multiple_images"`
			OpenNow        int             `json:"open_now"`
			Providers      []ProviderCount `json:"providers"`
			MinScrapeDate  string          `json:"min_scrape_date"`
			MaxScrapeDate  string          `json:"max_scrape_date"`
		}

		var stats FilterStats

		db.QueryRow(`SELECT COUNT(*) FROM scraped_cafes`).Scan(&stats.Total)

		db.QueryRow(`
			SELECT
				COALESCE(SUM(CASE WHEN img_count >= 1 THEN 1 ELSE 0 END), 0),
				COALESCE(SUM(CASE WHEN img_count >= 2 THEN 1 ELSE 0 END), 0)
			FROM (SELECT cafe_id, COUNT(*) as img_count FROM images GROUP BY cafe_id)
		`).Scan(&stats.WithImages, &stats.MultipleImages)

		db.QueryRow(`
			SELECT COUNT(*) FROM scraped_cafes
			WHERE json_extract(metadata, '$.businessStatus.status.code') = 2
		`).Scan(&stats.OpenNow)

		var minDate, maxDate sql.NullString
		db.QueryRow(`SELECT MIN(scraped_at), MAX(scraped_at) FROM scraped_cafes`).Scan(&minDate, &maxDate)
		if minDate.Valid {
			stats.MinScrapeDate = minDate.String
		}
		if maxDate.Valid {
			stats.MaxScrapeDate = maxDate.String
		}

		provRows, err := db.Query(`SELECT provider, COUNT(*) FROM scraped_cafes GROUP BY provider ORDER BY COUNT(*) DESC`)
		if err == nil {
			defer provRows.Close()
			for provRows.Next() {
				var pc ProviderCount
				provRows.Scan(&pc.Name, &pc.Count)
				stats.Providers = append(stats.Providers, pc)
			}
		}

		corsJSON(w)
		json.NewEncoder(w).Encode(stats)
	})


	// ── Split status sections ─────────────────────────────────────────────────
	// The frontend fans out to these per-section endpoints and renders each as it
	// lands, instead of blocking on one monolithic response whose slowest clean.db
	// scan (funnel ~22s) held up the whole panel. Each section has its own cache.

	// GET /api/stats/overview — summary cards, services, disk, queue. Fast (~300ms).
	mux.HandleFunc("/api/stats/overview", func(w http.ResponseWriter, r *http.Request) {
		corsJSON(w)
		statsOverviewCache.serve(w, 8*time.Second, func() (interface{}, error) {
			var o StatsOverview
			o.Services = getCachedServices()
			now := time.Now().UTC()
			h1ago := now.Add(-1 * time.Hour).Format("2006-01-02 15:04:05")
			h24ago := now.Add(-24 * time.Hour).Format("2006-01-02 15:04:05")
			rawDb.QueryRow(`SELECT COUNT(*) FROM scraped_cafes`).Scan(&o.TotalCafes)
			rawDb.QueryRow(`SELECT COUNT(*) FROM images`).Scan(&o.TotalImages)
			rawDb.QueryRow(`SELECT COUNT(*) FROM scraped_cafes WHERE scraped_at >= ?`, h1ago).Scan(&o.CafesLastHour)
			rawDb.QueryRow(`SELECT COUNT(*) FROM scraped_cafes WHERE scraped_at >= ?`, h24ago).Scan(&o.Cafes24h)
			rawDb.QueryRow(`SELECT COUNT(*) FROM images WHERE scraped_at >= ?`, h1ago).Scan(&o.ImagesLastHour)
			rawDb.QueryRow(`SELECT COUNT(*) FROM images WHERE scraped_at >= ?`, h24ago).Scan(&o.Images24h)
			rawDb.QueryRow(`SELECT COUNT(*) FROM images WHERE scraped_at >= ? AND file_size > 0`, h1ago).Scan(&o.DownloadedLastHour)
			rawDb.QueryRow(`SELECT COUNT(*) FROM images WHERE scraped_at >= ? AND file_size > 0`, h24ago).Scan(&o.Downloaded24h)
			rawDb.QueryRow(`SELECT ROUND(COALESCE(SUM(file_size),0)/1024.0/1024.0, 1) FROM images WHERE scraped_at >= ? AND file_size > 0`, h24ago).Scan(&o.MBPerDay)
			var lastCafe, lastImage sql.NullString
			rawDb.QueryRow(`SELECT MAX(scraped_at) FROM scraped_cafes`).Scan(&lastCafe)
			rawDb.QueryRow(`SELECT MAX(scraped_at) FROM images`).Scan(&lastImage)
			if lastCafe.Valid {
				o.LastCafeAt = lastCafe.String
			}
			if lastImage.Valid {
				o.LastImageAt = lastImage.String
			}
			o.Disk = getDiskStats(dataDir)
			o.DbQueue = getQueueStats(dataDir)
			return o, nil
		})
	})

	// GET /api/stats/providers — "Scraping Metrics" table (cafe + image throughput). Medium (~4.5s).
	mux.HandleFunc("/api/stats/providers", func(w http.ResponseWriter, r *http.Request) {
		corsJSON(w)
		statsProvidersCache.serve(w, 60*time.Second, func() (interface{}, error) {
			now := time.Now().UTC()
			h1ago := now.Add(-1 * time.Hour).Format("2006-01-02 15:04:05")
			h24ago := now.Add(-24 * time.Hour).Format("2006-01-02 15:04:05")
			pmMap := map[string]*ProviderCore{}
			order := []string{}
			rows, err := rawDb.Query(`
				SELECT
					provider,
					COUNT(*) as total,
					SUM(CASE WHEN scraped_at >= ? THEN 1 ELSE 0 END) as last_hour,
					SUM(CASE WHEN scraped_at >= ? THEN 1 ELSE 0 END) as last_24h,
					SUM(CASE WHEN json_extract(metadata, '$.website') IS NOT NULL AND json_extract(metadata, '$.website') != '' THEN 1 ELSE 0 END) as has_website
				FROM scraped_cafes GROUP BY provider ORDER BY total DESC
			`, h1ago, h24ago)
			if err != nil {
				return nil, err
			}
			for rows.Next() {
				pc := &ProviderCore{}
				rows.Scan(&pc.Provider, &pc.Total, &pc.CafesLastHour, &pc.Cafes24h, &pc.HasWebsite)
				pmMap[pc.Provider] = pc
				order = append(order, pc.Provider)
			}
			rows.Close()

			imgRows, err := rawDb.Query(`
				SELECT
					provider,
					SUM(CASE WHEN scraped_at >= ? THEN 1 ELSE 0 END) as last_hour,
					SUM(CASE WHEN scraped_at >= ? THEN 1 ELSE 0 END) as last_24h,
					SUM(CASE WHEN scraped_at >= ? AND file_size > 0 THEN 1 ELSE 0 END) as dl_last_hour,
					SUM(CASE WHEN scraped_at >= ? AND file_size > 0 THEN 1 ELSE 0 END) as dl_24h
				FROM images GROUP BY provider
			`, h1ago, h24ago, h1ago, h24ago)
			if err == nil {
				for imgRows.Next() {
					var prov string
					var lh, l24, dlh, dl24 int
					imgRows.Scan(&prov, &lh, &l24, &dlh, &dl24)
					if pc, ok := pmMap[prov]; ok {
						pc.ImagesLastHour = lh
						pc.Images24h = l24
						pc.DownloadedLastHour = dlh
						pc.Downloaded24h = dl24
					}
				}
				imgRows.Close()
			}
			out := StatsProviders{}
			for _, p := range order {
				out.PerProvider = append(out.PerProvider, *pmMap[p])
			}
			return out, nil
		})
	})

	// GET /api/stats/coverage — "Image Coverage" table (per-cafe image distribution). Slow (~7s).
	mux.HandleFunc("/api/stats/coverage", func(w http.ResponseWriter, r *http.Request) {
		corsJSON(w)
		statsCoverageCache.serve(w, 120*time.Second, func() (interface{}, error) {
			rows, err := rawDb.Query(`
				SELECT
					provider,
					COUNT(DISTINCT cafe_id) as cafes_with_images,
					SUM(CASE WHEN img_count >= 2  THEN 1 ELSE 0 END) as cafes_2plus,
					SUM(CASE WHEN img_count >= 10 THEN 1 ELSE 0 END) as cafes_10plus,
					SUM(CASE WHEN img_count >= 50 THEN 1 ELSE 0 END) as cafes_50plus,
					ROUND(AVG(img_count), 1) as avg_images,
					SUM(img_count) as total_images
				FROM (
					SELECT cafe_id, provider, COUNT(*) as img_count
					FROM images GROUP BY cafe_id, provider
				)
				GROUP BY provider
			`)
			if err != nil {
				return nil, err
			}
			defer rows.Close()
			out := StatsCoverage{}
			for rows.Next() {
				var c ProviderCoverage
				rows.Scan(&c.Provider, &c.CafesWithImages, &c.Cafes2Plus, &c.Cafes10Plus, &c.Cafes50Plus, &c.AvgImages, &c.TotalImages)
				out.PerProvider = append(out.PerProvider, c)
			}
			return out, nil
		})
	})

	// GET /api/stats/hourly — 24h activity chart series (overall + per-provider). Medium (~1.5s).
	mux.HandleFunc("/api/stats/hourly", func(w http.ResponseWriter, r *http.Request) {
		corsJSON(w)
		statsHourlyCache.serve(w, 30*time.Second, func() (interface{}, error) {
			now := time.Now().UTC()
			h24ago := now.Add(-24 * time.Hour).Format("2006-01-02 15:04:05")
			hours := make([]string, 24)
			for i := 23; i >= 0; i-- {
				hours[23-i] = now.Add(-time.Duration(i) * time.Hour).Format("2006-01-02 15:00:00")
			}
			allMap := make(map[string]*HourlyStat, 24)
			for _, h := range hours {
				allMap[h] = &HourlyStat{Hour: h, Provider: "all"}
			}
			if cafeRows, err := rawDb.Query(`
				SELECT strftime('%Y-%m-%d %H:00:00', scraped_at) as hour, COUNT(*)
				FROM scraped_cafes WHERE scraped_at >= ? GROUP BY hour
			`, h24ago); err == nil {
				for cafeRows.Next() {
					var h string
					var c int
					cafeRows.Scan(&h, &c)
					if s, ok := allMap[h]; ok {
						s.Cafes = c
					}
				}
				cafeRows.Close()
			}
			if imgRows, err := rawDb.Query(`
				SELECT strftime('%Y-%m-%d %H:00:00', scraped_at) as hour, COUNT(*)
				FROM images WHERE scraped_at >= ? GROUP BY hour
			`, h24ago); err == nil {
				for imgRows.Next() {
					var h string
					var c int
					imgRows.Scan(&h, &c)
					if s, ok := allMap[h]; ok {
						s.Images = c
					}
				}
				imgRows.Close()
			}

			// Per-provider blocks — discovered from the rows themselves (no
			// dependency on the providers section).
			provOrder := []string{}
			provMaps := map[string]map[string]*HourlyStat{}
			ensure := func(p string) map[string]*HourlyStat {
				m, ok := provMaps[p]
				if !ok {
					m = make(map[string]*HourlyStat, 24)
					for _, h := range hours {
						m[h] = &HourlyStat{Hour: h, Provider: p}
					}
					provMaps[p] = m
					provOrder = append(provOrder, p)
				}
				return m
			}
			if pcRows, err := rawDb.Query(`
				SELECT provider, strftime('%Y-%m-%d %H:00:00', scraped_at) as hour, COUNT(*)
				FROM scraped_cafes WHERE scraped_at >= ? GROUP BY provider, hour
			`, h24ago); err == nil {
				for pcRows.Next() {
					var p, h string
					var c int
					pcRows.Scan(&p, &h, &c)
					if s, ok := ensure(p)[h]; ok {
						s.Cafes = c
					}
				}
				pcRows.Close()
			}
			if piRows, err := rawDb.Query(`
				SELECT provider, strftime('%Y-%m-%d %H:00:00', scraped_at) as hour, COUNT(*)
				FROM images WHERE scraped_at >= ? GROUP BY provider, hour
			`, h24ago); err == nil {
				for piRows.Next() {
					var p, h string
					var c int
					piRows.Scan(&p, &h, &c)
					if s, ok := ensure(p)[h]; ok {
						s.Images = c
					}
				}
				piRows.Close()
			}

			out := StatsHourly{}
			for _, h := range hours {
				out.HourlyStats = append(out.HourlyStats, *allMap[h])
			}
			for _, p := range provOrder {
				for _, h := range hours {
					out.HourlyStats = append(out.HourlyStats, *provMaps[p][h])
				}
			}
			return out, nil
		})
	})

	// GET /api/stats/tagging — image-tagger progress (clean.db). Slow (~10s, COUNT DISTINCT).
	mux.HandleFunc("/api/stats/tagging", func(w http.ResponseWriter, r *http.Request) {
		corsJSON(w)
		statsTaggingCache.serve(w, 120*time.Second, func() (interface{}, error) {
			now := time.Now().UTC()
			h1agoISO := now.Add(-1 * time.Hour).Format("2006-01-02T15:04:05")
			var t StatsTagging
			db.QueryRow(`SELECT COUNT(DISTINCT image_id) FROM image_tags WHERE tagger IS NOT NULL`).Scan(&t.OverallTaggedImages)
			var imgsLastHour int
			db.QueryRow(`SELECT COUNT(DISTINCT image_id) FROM image_tags WHERE tagger IS NOT NULL AND tagged_at >= ?`, h1agoISO).Scan(&imgsLastHour)
			t.OverallImgsPerHour = float64(imgsLastHour)
			return t, nil
		})
	})

	// GET /api/stats/funnel — pipeline funnel (clean.db image scans). Slowest (~25s).
	mux.HandleFunc("/api/stats/funnel", func(w http.ResponseWriter, r *http.Request) {
		corsJSON(w)
		statsFunnelCache.serve(w, 120*time.Second, func() (interface{}, error) {
			var f StatsFunnel
			var totalCafes, mergedScraped int
			rawDb.QueryRow(`SELECT COUNT(*) FROM scraped_cafes`).Scan(&totalCafes)
			db.QueryRow(`SELECT COUNT(*) FROM clean_cafes`).Scan(&f.MergedCafes)
			db.QueryRow(`SELECT COUNT(*) FROM scraped_cafes WHERE belongs_to_cafe_id IS NOT NULL`).Scan(&mergedScraped)
			if f.MergeQueue = totalCafes - mergedScraped; f.MergeQueue < 0 {
				f.MergeQueue = 0
			}
			db.QueryRow(`SELECT COUNT(*) FROM images`).Scan(&f.ImagesTotal)
			db.QueryRow(`SELECT COUNT(*) FROM images WHERE file_size > 0`).Scan(&f.ImagesDownloaded)
			return f, nil
		})
	})

	scraperDir := os.Getenv("SCRAPER_DIR")
	if scraperDir == "" {
		scraperDir = "../scraper"
	}

	// ── /api/services/{name}/{action|log} ────────────────────────────────────
	serviceLogFiles := map[string]string{
		"db-server":      "log/db_server.log",
		"kakao":          "log/scraper_kakao_v2.log",
		"google":         "log/scraper_google_v2.log",
		"naver":          "log/scraper_naver.log",
		"osm":            "log/scraper_osm.log",
		"kakao-images":   "log/scraper_kakao_images_v3.log",
		"naver-images":   "log/scraper_naver_images_v1.log",
		"google-images":  "log/scraper_google_images_v1.log",
		"kakao-metadata": "log/scraper_kakao_metadata_v1.log",
		"naver-metadata": "log/scraper_naver_metadata_v1.log",
	}
	mux.HandleFunc("/api/services/", func(w http.ResponseWriter, r *http.Request) {
		corsJSON(w)
		if r.Method == "OPTIONS" {
			w.WriteHeader(204)
			return
		}

		parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/api/services/"), "/")

		// GET /api/services/{name}/log
		if r.Method == http.MethodGet && len(parts) == 2 && parts[1] == "log" {
			name := parts[0]
			logFile, ok := serviceLogFiles[name]
			if !ok {
				http.Error(w, "unknown service: "+name, 404)
				return
			}
			nLines := 30
			if n, err := strconv.Atoi(r.URL.Query().Get("lines")); err == nil && n > 0 && n <= 2000 {
				nLines = n
			}
			logPath := filepath.Join(scraperDir, logFile)
			data, err := os.ReadFile(logPath)
			if err != nil {
				if os.IsNotExist(err) {
					json.NewEncoder(w).Encode(map[string]interface{}{"lines": []string{}, "path": logPath})
					return
				}
				http.Error(w, err.Error(), 500)
				return
			}
			all := strings.Split(strings.TrimRight(string(data), "\n"), "\n")
			start := 0
			if len(all) > nLines {
				start = len(all) - nLines
			}
			json.NewEncoder(w).Encode(map[string]interface{}{
				"lines": all[start:],
				"total": len(all),
				"path":  logPath,
			})
			return
		}

		if r.Method != "POST" {
			http.Error(w, "method not allowed", 405)
			return
		}

		// Parse /api/services/{name}/{action}
		if len(parts) != 2 {
			http.Error(w, "usage: /api/services/{name}/{start|stop|restart}", 400)
			return
		}
		name, action := parts[0], parts[1]

		unit, ok := serviceMap[name]
		if !ok {
			http.Error(w, fmt.Sprintf("unknown service %q, valid: %v", name, serviceOrder), 404)
			return
		}
		if action != "start" && action != "stop" && action != "restart" {
			http.Error(w, "action must be start, stop, or restart", 400)
			return
		}

		out, err := exec.Command("systemctl", "--user", action, unit).CombinedOutput()
		result := map[string]interface{}{
			"service": name,
			"unit":    unit,
			"action":  action,
			"ok":      err == nil,
			"output":  strings.TrimSpace(string(out)),
		}
		if err != nil {
			result["error"] = err.Error()
		}

		// Persist intent across reboots
		if err == nil {
			if action == "start" {
				exec.Command("systemctl", "--user", "enable", unit).Run()
			} else if action == "stop" {
				exec.Command("systemctl", "--user", "disable", unit).Run()
			}
		}

		// Return fresh state
		state, active := getServiceState(unit)
		result["state"] = state
		result["active"] = active

		// Keep watchdog timer in sync with image scraper lifecycle
		if imageScraperNames[name] {
			go syncWatchdog()
		}

		json.NewEncoder(w).Encode(result)
	})


	// ── GET /api/gscraper/stats ───────────────────────────────────────────────
	mux.HandleFunc("/api/gscraper/stats", func(w http.ResponseWriter, r *http.Request) {
		corsJSON(w)
		statsPath := filepath.Join(scraperDir, "google-proxy-stats.json")
		data, err := os.ReadFile(statsPath)
		if err != nil {
			if os.IsNotExist(err) {
				w.Write([]byte(`{"summary":{},"events":[]}`))
				return
			}
			http.Error(w, err.Error(), 500)
			return
		}
		w.Write(data)
	})

	// ── GET /api/gscraper/log?lines=N ─────────────────────────────────────────
	mux.HandleFunc("/api/gscraper/log", func(w http.ResponseWriter, r *http.Request) {
		corsJSON(w)
		nLines := 200
		if n, err := strconv.Atoi(r.URL.Query().Get("lines")); err == nil && n > 0 && n <= 2000 {
			nLines = n
		}
		logPath := filepath.Join(scraperDir, "log", "scraper_google_images_v1.log")
		data, err := os.ReadFile(logPath)
		if err != nil {
			if os.IsNotExist(err) {
				json.NewEncoder(w).Encode(map[string]interface{}{"lines": []string{}, "path": logPath})
				return
			}
			http.Error(w, err.Error(), 500)
			return
		}
		all := strings.Split(strings.TrimRight(string(data), "\n"), "\n")
		start := 0
		if len(all) > nLines {
			start = len(all) - nLines
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"lines": all[start:],
			"total": len(all),
			"path":  logPath,
		})
	})

	// ── GET /api/watchdog-status ─────────────────────────────────────────────
	mux.HandleFunc("/api/watchdog-status", func(w http.ResponseWriter, r *http.Request) {
		corsJSON(w)
		statusPath := filepath.Join(filepath.Dir(dataDir), "watchdog-status.json")
		data, err := os.ReadFile(statusPath)
		if err != nil {
			if os.IsNotExist(err) {
				w.Write([]byte(`{"services":{},"updated_at":null}`))
				return
			}
			http.Error(w, err.Error(), 500)
			return
		}
		w.Write(data)
	})

	// ── GET /api/chains ────────────────────────────────────────────────────────
	mux.HandleFunc("/api/chains", func(w http.ResponseWriter, r *http.Request) {
		corsJSON(w)
		sdb := snapshots.dbForRequest(r, db)
		rows, err := sdb.Query(`
			SELECT c.id, c.name, c.name_english, COUNT(cc.id) as count
			FROM cafe_chains c
			JOIN clean_cafes cc ON c.id = cc.chain_id
			GROUP BY c.id
			ORDER BY count DESC
			LIMIT 100
		`)
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		defer rows.Close()

		type Chain struct {
			ID          string `json:"id"`
			Name        string `json:"name"`
			NameEnglish string `json:"name_english"`
			Count       int    `json:"count"`
		}

		chains := make([]Chain, 0)
		for rows.Next() {
			var c Chain
			var ne *string
			if err := rows.Scan(&c.ID, &c.Name, &ne, &c.Count); err != nil {
				http.Error(w, err.Error(), 500)
				return
			}
			if ne != nil {
				c.NameEnglish = *ne
			}
			chains = append(chains, c)
		}

		json.NewEncoder(w).Encode(chains)
	})

	// ── GET /api/tags ────────────────────────────────────────────────────────
	mux.HandleFunc("/api/tags", func(w http.ResponseWriter, r *http.Request) {
		sdb := snapshots.dbForRequest(r, db)
		rows, err := sdb.Query(`
			SELECT je.key as tag, COUNT(*) as cafe_count
			FROM clean_cafes, json_each(tags) je
			WHERE tags IS NOT NULL
			GROUP BY je.key
			ORDER BY cafe_count DESC`)
		if err != nil {
			corsJSON(w)
			json.NewEncoder(w).Encode([]interface{}{})
			return
		}
		defer rows.Close()
		type TagCount struct {
			Tag   string `json:"tag"`
			Count int    `json:"count"`
		}
		result := make([]TagCount, 0)
		for rows.Next() {
			var t TagCount
			rows.Scan(&t.Tag, &t.Count)
			result = append(result, t)
		}
		corsJSON(w)
		json.NewEncoder(w).Encode(result)
	})

	// ── GET /api/image-tags — tag list from image_tags table ─────────────────
	mux.HandleFunc("/api/image-tags", func(w http.ResponseWriter, r *http.Request) {
		sdb := snapshots.dbForRequest(r, db)
		rows, err := sdb.Query(`
			SELECT tag, COUNT(*) as cnt
			FROM image_tags
			GROUP BY tag
			ORDER BY cnt DESC`)
		type TagCount struct {
			Tag   string `json:"tag"`
			Count int    `json:"count"`
		}
		result := make([]TagCount, 0)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var t TagCount
				rows.Scan(&t.Tag, &t.Count)
				result = append(result, t)
			}
		}
		corsJSON(w)
		json.NewEncoder(w).Encode(result)
	})

	// ── GET /api/tag-images?tag=X&limit=500 — images for tag, score DESC ─────
	mux.HandleFunc("/api/tag-images", func(w http.ResponseWriter, r *http.Request) {
		sdb := snapshots.dbForRequest(r, db)
		tag := r.URL.Query().Get("tag")
		if tag == "" {
			corsJSON(w)
			json.NewEncoder(w).Encode([]interface{}{})
			return
		}
		limit := 500
		type TagImage struct {
			ImageID   int             `json:"image_id"`
			CafeID    string          `json:"cafe_id"`
			LocalPath string          `json:"local_path"`
			Score     float64         `json:"score"`
			Boxes     json.RawMessage `json:"boxes"`
		}
		rows, err := sdb.Query(`
			SELECT it.image_id, i.cafe_id, i.local_path, it.score,
			       json(COALESCE(it.boxes,'null'))
			FROM image_tags it
			JOIN images i ON i.id = it.image_id
			WHERE it.tag = ?
			ORDER BY it.score DESC
			LIMIT ?`, tag, limit)
		result := make([]TagImage, 0)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var ti TagImage
				var boxesStr string
				rows.Scan(&ti.ImageID, &ti.CafeID, &ti.LocalPath, &ti.Score, &boxesStr)
				if boxesStr == "" {
					boxesStr = "null"
				}
				ti.Boxes = json.RawMessage(boxesStr)
				result = append(result, ti)
			}
		}
		corsJSON(w)
		json.NewEncoder(w).Encode(result)
	})

	// ── GET /api/clean_cafes ─────────────────────────────────────────────────
	mux.HandleFunc("/api/clean_cafes", func(w http.ResponseWriter, r *http.Request) {
		sdb := snapshots.dbForRequest(r, db)
		q := r.URL.Query()
		const limit = 1000
		var conditions []string
		var args []interface{}

		if minLat, maxLat, minLon, maxLon := q.Get("minLat"), q.Get("maxLat"), q.Get("minLon"), q.Get("maxLon"); minLat != "" {
			conditions = append(conditions, "cc.avg_lat BETWEEN ? AND ? AND cc.avg_lon BETWEEN ? AND ?")
			args = append(args, minLat, maxLat, minLon, maxLon)
		}

		if q.Get("withImages") == "true" {
			conditions = append(conditions, `(
				SELECT COUNT(*) FROM images i
				JOIN scraped_cafes c ON c.id = i.cafe_id
				WHERE c.belongs_to_cafe_id = cc.id AND i.file_size > 0
			) >= 1`)
		}
		if q.Get("multipleImages") == "true" {
			conditions = append(conditions, `(
				SELECT COUNT(*) FROM images i
				JOIN scraped_cafes c ON c.id = i.cafe_id
				WHERE c.belongs_to_cafe_id = cc.id AND i.file_size > 0
			) >= 2`)
		}

		if providers := q.Get("providers"); providers != "" {
			// filter: clean_cafe must have at least one of these providers
			provList := strings.Split(providers, ",")
			var provConds []string
			for _, p := range provList {
				args = append(args, "%\""+p+"\"%")
				provConds = append(provConds, "cc.providers LIKE ?")
			}
			conditions = append(conditions, "("+strings.Join(provConds, " OR ")+")")
		}

		if chains := q.Get("chains"); chains != "" {
			chainList := strings.Split(chains, ",")
			var placeholders []string
			for _, c := range chainList {
				args = append(args, c)
				placeholders = append(placeholders, "?")
			}
			conditions = append(conditions, "cc.chain_id IN ("+strings.Join(placeholders, ",")+")")
		}

		if tags := q.Get("tags"); tags != "" {
			for _, tag := range strings.Split(tags, ",") {
				args = append(args, "$."+strings.TrimSpace(tag))
				conditions = append(conditions, "json_extract(cc.tags, ?) IS NOT NULL")
			}
		}

		if q.Get("customWebsite") == "true" {
			conditions = append(conditions, "cc.has_custom_website = 1")
		}

		where := ""
		if len(conditions) > 0 {
			where = "WHERE " + strings.Join(conditions, " AND ")
		}

		countArgs := make([]interface{}, len(args))
		copy(countArgs, args)
		var total int
		sdb.QueryRow("SELECT COUNT(*) FROM clean_cafes cc "+where, countArgs...).Scan(&total)

		rows, err := sdb.Query(`
			SELECT cc.id, cc.name, COALESCE(cc.english_name,''), cc.avg_lat, cc.avg_lon,
			       COALESCE(cc.providers,'[]'), COALESCE(cc.source_ids,'[]'),
			       COALESCE(cc.address,''), COALESCE(cc.url,''),
			       COALESCE(ch.name,''), COALESCE(ch.name_english,''),
			       (SELECT COUNT(*) FROM images i JOIN scraped_cafes c ON c.id = i.cafe_id
			        WHERE c.belongs_to_cafe_id = cc.id AND i.file_size > 0) as img_count
			FROM clean_cafes cc
			LEFT JOIN cafe_chains ch ON ch.id = cc.chain_id
			`+where+`
			ORDER BY RANDOM() LIMIT ?`, append(args, limit)...)
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		defer rows.Close()

		type CleanCafe struct {
			ID          string `json:"id"`
			Name        string `json:"name"`
			EnglishName string `json:"english_name,omitempty"`
			Lat         float64 `json:"lat"`
			Lon         float64 `json:"lon"`
			Providers   json.RawMessage `json:"providers"`
			SourceIDs   json.RawMessage `json:"source_ids"`
			Address     string `json:"address"`
			URL         string `json:"url"`
			ChainName   string `json:"chain_name,omitempty"`
			ChainNameEN string `json:"chain_name_english,omitempty"`
			ImageCount  int    `json:"image_count"`
		}

		scraped_cafes := make([]CleanCafe, 0, limit)
		for rows.Next() {
			var cc CleanCafe
			var provJSON, srcJSON string
			if err := rows.Scan(&cc.ID, &cc.Name, &cc.EnglishName, &cc.Lat, &cc.Lon,
				&provJSON, &srcJSON, &cc.Address, &cc.URL,
				&cc.ChainName, &cc.ChainNameEN, &cc.ImageCount); err != nil {
				http.Error(w, err.Error(), 500)
				return
			}
			cc.Providers = json.RawMessage(provJSON)
			cc.SourceIDs = json.RawMessage(srcJSON)
			scraped_cafes = append(scraped_cafes, cc)
		}

		corsJSON(w)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"scraped_cafes":   scraped_cafes,
			"showing": len(scraped_cafes),
			"total":   total,
		})
	})

	// ── GET /api/clean_cafe?id=... ────────────────────────────────────────────
	mux.HandleFunc("/api/clean_cafe", func(w http.ResponseWriter, r *http.Request) {
		sdb := snapshots.dbForRequest(r, db)
		id := r.URL.Query().Get("id")
		if id == "" {
			http.Error(w, "id required", 400)
			return
		}

		// Clean cafe base info
		row := sdb.QueryRow(`
			SELECT cc.id, cc.name, COALESCE(cc.english_name,''), cc.avg_lat, cc.avg_lon,
			       COALESCE(cc.providers,'[]'), COALESCE(cc.source_ids,'[]'),
			       COALESCE(cc.address,''), COALESCE(cc.url,''),
			       COALESCE(ch.name,''), COALESCE(ch.name_english,''), COALESCE(ch.id,'')
			FROM clean_cafes cc
			LEFT JOIN cafe_chains ch ON ch.id = cc.chain_id
			WHERE cc.id = ?`, id)

		var cleanID, name, englishName, address, url, chainName, chainNameEN, chainID string
		var avgLat, avgLon float64
		var provJSON, srcJSON string
		if err := row.Scan(&cleanID, &name, &englishName, &avgLat, &avgLon,
			&provJSON, &srcJSON, &address, &url,
			&chainName, &chainNameEN, &chainID); err != nil {
			http.Error(w, "not found", 404)
			return
		}

		// Source scraped_cafes
		sourceRows, err := sdb.Query(`
			SELECT c.id, COALESCE(c.provider,''), COALESCE(c.name,''),
			       c.lat, c.lon, COALESCE(c.address,''), COALESCE(c.url,''),
			       COALESCE(c.metadata,'null'), COALESCE(c.scraped_at,'')
			FROM scraped_cafes c WHERE c.belongs_to_cafe_id = ?`, id)
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		defer sourceRows.Close()

		type SourceCafe struct {
			ID        string          `json:"id"`
			Provider  string          `json:"provider"`
			Name      string          `json:"name"`
			Lat       float64         `json:"lat"`
			Lon       float64         `json:"lon"`
			Address   string          `json:"address"`
			URL       string          `json:"url"`
			Metadata  json.RawMessage `json:"metadata"`
			ScrapedAt string          `json:"scraped_at"`
			Images    json.RawMessage `json:"images"`
		}

		sources := make([]SourceCafe, 0)
		var sourceIDs []string
		for sourceRows.Next() {
			var s SourceCafe
			var meta string
			if err := sourceRows.Scan(&s.ID, &s.Provider, &s.Name, &s.Lat, &s.Lon,
				&s.Address, &s.URL, &meta, &s.ScrapedAt); err != nil {
				continue
			}
			s.Metadata = json.RawMessage(meta)
			sourceIDs = append(sourceIDs, s.ID)
			sources = append(sources, s)
		}

		// Images for all source scraped_cafes
		if len(sourceIDs) > 0 {
			placeholders := make([]string, len(sourceIDs))
			imgArgs := make([]interface{}, len(sourceIDs))
			for i, sid := range sourceIDs {
				placeholders[i] = "?"
				imgArgs[i] = sid
			}
			imgRows, err := sdb.Query(`
				SELECT i.id, i.cafe_id, i.provider, i.local_path, i.image_url, i.photo_id,
				       COALESCE(i.width,0), COALESCE(i.height,0), i.file_size, COALESCE(i.scraped_at,''),
				       COALESCE((SELECT json_group_array(json_object('tag',tag,'score',score,'boxes',json(COALESCE(boxes,'null')))) FROM image_tags WHERE image_id = i.id), '[]'),
				       COALESCE(i.tagged_at,'')
				FROM images i
				WHERE i.cafe_id IN (`+strings.Join(placeholders, ",")+`) AND i.file_size > 0
				ORDER BY i.cafe_id, i.scraped_at DESC`, imgArgs...)
			if err == nil {
				defer imgRows.Close()
				type ImageInfo struct {
					ID        int             `json:"id"`
					CafeID    string          `json:"cafe_id"`
					Provider  string          `json:"provider"`
					LocalPath string          `json:"local_path"`
					ImageURL  string          `json:"image_url"`
					PhotoID   string          `json:"photo_id"`
					Width     int             `json:"width"`
					Height    int             `json:"height"`
					FileSize  int             `json:"file_size"`
					ScrapedAt string          `json:"scraped_at"`
					Tags      json.RawMessage `json:"tags"`
					TaggedAt  string          `json:"tagged_at"`
				}
				imagesByCafe := map[string][]ImageInfo{}
				allImages := make([]ImageInfo, 0)
				for imgRows.Next() {
					var img ImageInfo
					var tagsStr string
					imgRows.Scan(&img.ID, &img.CafeID, &img.Provider, &img.LocalPath,
						&img.ImageURL, &img.PhotoID, &img.Width, &img.Height,
						&img.FileSize, &img.ScrapedAt, &tagsStr, &img.TaggedAt)
					if tagsStr == "" {
						tagsStr = "[]"
					}
					img.Tags = json.RawMessage(tagsStr)
					imagesByCafe[img.CafeID] = append(imagesByCafe[img.CafeID], img)
					allImages = append(allImages, img)
				}
				for i := range sources {
					imgs := imagesByCafe[sources[i].ID]
					if imgs == nil {
						imgs = []ImageInfo{}
					}
					b, _ := json.Marshal(imgs)
					sources[i].Images = json.RawMessage(b)
				}

				corsJSON(w)
				json.NewEncoder(w).Encode(map[string]interface{}{
					"id":               cleanID,
					"name":             name,
					"english_name":     englishName,
					"avg_lat":          avgLat,
					"avg_lon":          avgLon,
					"providers":        json.RawMessage(provJSON),
					"source_ids":       json.RawMessage(srcJSON),
					"address":          address,
					"url":              url,
					"chain_name":       chainName,
					"chain_name_english": chainNameEN,
					"chain_id":         chainID,
					"sources":          sources,
					"all_images":       allImages,
					"image_count":      len(allImages),
				})
				return
			}
		}

		corsJSON(w)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"id": cleanID, "name": name, "english_name": englishName,
			"avg_lat": avgLat, "avg_lon": avgLon,
			"providers": json.RawMessage(provJSON),
			"sources": sources, "all_images": []interface{}{},
		})
	})

	// ── GET /api/custom-websites ──────────────────────────────────────────────
	mux.HandleFunc("/api/custom-websites", func(w http.ResponseWriter, r *http.Request) {
		sdb := snapshots.dbForRequest(r, db)
		rows, err := sdb.Query(`
			SELECT cc.id, cc.name, COALESCE(cc.english_name,''), cc.avg_lat, cc.avg_lon,
			       COALESCE(cc.address,''), cc.custom_website_url,
			       (SELECT COUNT(*) FROM images i JOIN scraped_cafes c ON c.id = i.cafe_id
			        WHERE c.belongs_to_cafe_id = cc.id AND i.file_size > 0) as img_count
			FROM clean_cafes cc
			WHERE cc.has_custom_website = 1
			ORDER BY cc.name`)
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		defer rows.Close()

		type CafeWithSite struct {
			ID          string  `json:"id"`
			Name        string  `json:"name"`
			EnglishName string  `json:"english_name,omitempty"`
			Lat         float64 `json:"lat"`
			Lon         float64 `json:"lon"`
			Address     string  `json:"address"`
			WebsiteURL  string  `json:"website_url"`
			ImageCount  int     `json:"image_count"`
		}

		result := make([]CafeWithSite, 0)
		for rows.Next() {
			var c CafeWithSite
			if err := rows.Scan(&c.ID, &c.Name, &c.EnglishName, &c.Lat, &c.Lon, &c.Address, &c.WebsiteURL, &c.ImageCount); err != nil {
				http.Error(w, err.Error(), 500)
				return
			}
			result = append(result, c)
		}

		corsJSON(w)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"cafes": result,
			"total": len(result),
		})
	})

	// ── GET /api/heatmap ─────────────────────────────────────────────────────
	mux.HandleFunc("/api/heatmap", func(w http.ResponseWriter, r *http.Request) {
		sdb := snapshots.dbForRequest(r, db)
		q := r.URL.Query()
		var conditions []string
		var args []interface{}

		if minLat := q.Get("minLat"); minLat != "" {
			conditions = append(conditions, "cc.avg_lat BETWEEN ? AND ? AND cc.avg_lon BETWEEN ? AND ?")
			args = append(args, minLat, q.Get("maxLat"), q.Get("minLon"), q.Get("maxLon"))
		}
		if q.Get("withImages") == "true" {
			conditions = append(conditions, `(SELECT COUNT(*) FROM images i JOIN scraped_cafes c ON c.id = i.cafe_id WHERE c.belongs_to_cafe_id = cc.id AND i.file_size > 0) >= 1`)
		}
		if q.Get("multipleImages") == "true" {
			conditions = append(conditions, `(SELECT COUNT(*) FROM images i JOIN scraped_cafes c ON c.id = i.cafe_id WHERE c.belongs_to_cafe_id = cc.id AND i.file_size > 0) >= 2`)
		}
		if providers := q.Get("providers"); providers != "" {
			provList := strings.Split(providers, ",")
			var provConds []string
			for _, p := range provList {
				args = append(args, "%\""+p+"\"%")
				provConds = append(provConds, "cc.providers LIKE ?")
			}
			conditions = append(conditions, "("+strings.Join(provConds, " OR ")+")")
		}
		if chains := q.Get("chains"); chains != "" {
			chainList := strings.Split(chains, ",")
			var placeholders []string
			for _, c := range chainList {
				args = append(args, c)
				placeholders = append(placeholders, "?")
			}
			conditions = append(conditions, "cc.chain_id IN ("+strings.Join(placeholders, ",")+")")
		}
		if tags := q.Get("tags"); tags != "" {
			for _, tag := range strings.Split(tags, ",") {
				args = append(args, "$."+strings.TrimSpace(tag))
				conditions = append(conditions, "json_extract(cc.tags, ?) IS NOT NULL")
			}
		}
		if q.Get("customWebsite") == "true" {
			conditions = append(conditions, "cc.has_custom_website = 1")
		}

		where := ""
		if len(conditions) > 0 {
			where = "WHERE " + strings.Join(conditions, " AND ")
		}

		rows, err := sdb.Query("SELECT cc.avg_lat, cc.avg_lon FROM clean_cafes cc "+where, args...)
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		defer rows.Close()

		type Point = [2]float64
		points := make([]Point, 0, 5000)
		for rows.Next() {
			var lat, lon float64
			if err := rows.Scan(&lat, &lon); err != nil {
				continue
			}
			points = append(points, Point{lat, lon})
		}

		corsJSON(w)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"points": points,
			"total":  len(points),
		})
	})

	// ── GET /api/scrape-coverage ──────────────────────────────────────────────
	// Read-only visualization of WHAT got scraped, WHERE, per provider — "snail style".
	// Cells = union of the progress table (per-provider completed cells) and cells that
	// contain scraped_cafes (binned by 0.01° around the grid origin = Seoul City Hall).
	mux.HandleFunc("/api/scrape-coverage", func(w http.ResponseWriter, r *http.Request) {
		corsJSON(w)
		q := r.URL.Query()

		// Optional region filter → restrict cells to the region's bbox.
		minGX, maxGX, minGY, maxGY := math.MinInt32, math.MaxInt32, math.MinInt32, math.MaxInt32
		if region := strings.ToLower(strings.TrimSpace(q.Get("region"))); region != "" {
			if c, ok := coverageRegions[region]; ok {
				cx := int(round((c.lon - coverageCenterLon) / coverageStep))
				cy := int(round((c.lat - coverageCenterLat) / coverageStep))
				// radius_km ≈ cells (each cell ~1km). Pad by 1.
				rad := c.radKm + 1
				minGX, maxGX = cx-rad, cx+rad
				minGY, maxGY = cy-rad, cy+rad
			}
		}
		// Optional explicit bbox (lat/lon) → grid bounds.
		if minLatS, maxLatS, minLonS, maxLonS := q.Get("minLat"), q.Get("maxLat"), q.Get("minLon"), q.Get("maxLon"); minLatS != "" && maxLatS != "" && minLonS != "" && maxLonS != "" {
			minLat, _ := strconv.ParseFloat(minLatS, 64)
			maxLat, _ := strconv.ParseFloat(maxLatS, 64)
			minLon, _ := strconv.ParseFloat(minLonS, 64)
			maxLon, _ := strconv.ParseFloat(maxLonS, 64)
			gx1 := int(round((minLon - coverageCenterLon) / coverageStep))
			gx2 := int(round((maxLon - coverageCenterLon) / coverageStep))
			gy1 := int(round((minLat - coverageCenterLat) / coverageStep))
			gy2 := int(round((maxLat - coverageCenterLat) / coverageStep))
			if gx1 > gx2 {
				gx1, gx2 = gx2, gx1
			}
			if gy1 > gy2 {
				gy1, gy2 = gy2, gy1
			}
			if gx1 > minGX {
				minGX = gx1
			}
			if gx2 < maxGX {
				maxGX = gx2
			}
			if gy1 > minGY {
				minGY = gy1
			}
			if gy2 < maxGY {
				maxGY = gy2
			}
		}

		// Read scraped.db (rawDb): the progress table + scraped_cafes there are live;
		// clean.db lags behind for scraper-activity data (see AGENTS.md).
		body := getScrapeCoverage(rawDb, minGX, maxGX, minGY, maxGY)
		w.Write(body)
	})

	addr := ":13854"
	log.Printf("Serving on %s (DB: %s)", addr, dbPath)
	log.Fatal(http.ListenAndServe(addr, mux))
}
