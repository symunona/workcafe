package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	_ "modernc.org/sqlite"
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
	Provider       string  `json:"provider"`
	CafesLastHour  int     `json:"cafes_last_hour"`
	Cafes24h       int     `json:"cafes_24h"`
	ImagesLastHour int     `json:"images_last_hour"`
	Images24h      int     `json:"images_24h"`
	Total          int     `json:"total"`
	// Image coverage distribution
	CafesWithImages int     `json:"cafes_with_images"`
	Cafes2Plus      int     `json:"cafes_2plus"`
	Cafes10Plus     int     `json:"cafes_10plus"`
	Cafes50Plus     int     `json:"cafes_50plus"`
	AvgImages       float64 `json:"avg_images"`
	TotalImages     int     `json:"total_images"`
}

type DiskStats struct {
	DataDirGB  float64 `json:"data_dir_gb"`
	LimitGB    float64 `json:"limit_gb"`
	UsedPct    float64 `json:"used_pct"`
	FreeGB     float64 `json:"free_gb"`
}

type QueueEntry struct {
	QueueDepth int    `json:"queue_depth"`
	UpdatedAt  string `json:"updated_at"`
}

type HourlyStat struct {
	Hour   string `json:"hour"`
	Cafes  int    `json:"cafes"`
	Images int    `json:"images"`
	Provider string `json:"provider"`
}

type StatusResponse struct {
	Services          []ServiceStatus        `json:"services"`
	PerProvider       []ProviderMetrics      `json:"per_provider"`
	FinishedProviders []string               `json:"finished_providers"`
	TotalCafes        int                    `json:"total_cafes"`
	TotalImages    int                    `json:"total_images"`
	CafesLastHour  int                    `json:"cafes_last_hour"`
	Cafes24h       int                    `json:"cafes_24h"`
	ImagesLastHour int                    `json:"images_last_hour"`
	Images24h      int                    `json:"images_24h"`
	LastCafeAt     string                 `json:"last_cafe_at"`
	LastImageAt    string                 `json:"last_image_at"`
	Disk           DiskStats              `json:"disk"`
	DbQueue        map[string]QueueEntry  `json:"db_queue"`
	HourlyStats    []HourlyStat           `json:"hourly_stats"`
}

var serviceMap = map[string]string{
	"db-server":     "workcafe-db-server",
	"api":           "workcafe-api",
	"frontend":      "workcafe-frontend",
	"kakao":         "workcafe-scraper-kakao",
	"google":        "workcafe-scraper-google",
	"osm":           "workcafe-scraper-osm",
	"naver":         "workcafe-scraper-naver",
	"kakao-images":  "workcafe-kakao-images",
	"naver-images":  "workcafe-naver-images",
	"google-images": "workcafe-google-images",
}

var serviceOrder = []string{"db-server", "api", "frontend", "kakao", "google", "osm", "naver", "kakao-images", "naver-images", "google-images"}

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

// Full status response cache — serves repeat hits instantly
var (
	statusCacheMu   sync.Mutex
	statusCacheBody []byte
	statusCachedAt  time.Time
)

const statusCacheTTL = 8 * time.Second

func getDiskStats(dataDir string) DiskStats {
	const ttl = 5 * time.Minute

	diskCacheMu.Lock()
	defer diskCacheMu.Unlock()
	if time.Since(diskCachedAt) < ttl {
		return diskCached
	}

	var stat syscall.Statfs_t
	err := syscall.Statfs(dataDir, &stat)
	if err != nil {
		return diskCached
	}

	freeBytes := float64(stat.Bavail) * float64(stat.Bsize)
	totalBytes := float64(stat.Blocks) * float64(stat.Bsize)
	usedBytes := totalBytes - float64(stat.Bfree) * float64(stat.Bsize)

	freeGB := freeBytes / (1024 * 1024 * 1024)
	totalGB := totalBytes / (1024 * 1024 * 1024)
	usedGB := usedBytes / (1024 * 1024 * 1024)

	var usedPct float64
	if totalGB > 0 {
		usedPct = (usedGB / totalGB) * 100
	}

	diskCached = DiskStats{
		DataDirGB: math_round2(usedGB),
		LimitGB:   math_round2(totalGB),
		UsedPct:   math_round2(usedPct),
		FreeGB:    math_round2(freeGB),
	}
	diskCachedAt = time.Now()
	return diskCached
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

func main() {
	dbPath := os.Getenv("DB_PATH")
	if dbPath == "" {
		dbPath = "../data/seoul/cafedata.db"
	}
	dataDir := os.Getenv("DATA_DIR")
	if dataDir == "" {
		dataDir = "../data/seoul"
	}

	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close()

	mux := http.NewServeMux()

	mux.Handle("/images/", http.StripPrefix("/images/", http.FileServer(http.Dir(dataDir))))

	// ── GET /api/cafes ────────────────────────────────────────────────────────
	mux.HandleFunc("/api/cafes", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()

		const limit = 1000
		var conditions []string
		var args []interface{}

		if minLat, maxLat, minLon, maxLon := q.Get("minLat"), q.Get("maxLat"), q.Get("minLon"), q.Get("maxLon"); minLat != "" && maxLat != "" && minLon != "" && maxLon != "" {
			conditions = append(conditions, "lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?")
			args = append(args, minLat, maxLat, minLon, maxLon)
		}

		if q.Get("multipleImages") == "true" {
			conditions = append(conditions, "(SELECT COUNT(*) FROM images WHERE cafe_id = cafes.id) >= 2")
		} else if q.Get("withImages") == "true" {
			conditions = append(conditions, "(SELECT COUNT(*) FROM images WHERE cafe_id = cafes.id) >= 1")
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
		db.QueryRow("SELECT COUNT(*) FROM cafes "+whereClause, countArgs...).Scan(&totalRows)

		dataArgs := append(args, limit)
		rows, err := db.Query("SELECT id, provider, provider_id, name, lat, lon, COALESCE(address,''), COALESCE(url,''), COALESCE(metadata,'null'), COALESCE(scraped_at,'') FROM cafes "+whereClause+" ORDER BY RANDOM() LIMIT ?", dataArgs...)
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		defer rows.Close()

		cafes := make([]Cafe, 0, limit)
		for rows.Next() {
			var c Cafe
			var meta string
			if err := rows.Scan(&c.ID, &c.Provider, &c.ProviderID, &c.Name, &c.Lat, &c.Lon, &c.Address, &c.URL, &meta, &c.ScrapedAt); err != nil {
				http.Error(w, err.Error(), 500)
				return
			}
			c.Metadata = json.RawMessage(meta)
			cafes = append(cafes, c)
		}

		corsJSON(w)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"cafes":   cafes,
			"showing": len(cafes),
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

		db.QueryRow(`SELECT COUNT(*) FROM cafes`).Scan(&stats.Total)

		db.QueryRow(`
			SELECT
				COALESCE(SUM(CASE WHEN img_count >= 1 THEN 1 ELSE 0 END), 0),
				COALESCE(SUM(CASE WHEN img_count >= 2 THEN 1 ELSE 0 END), 0)
			FROM (SELECT cafe_id, COUNT(*) as img_count FROM images GROUP BY cafe_id)
		`).Scan(&stats.WithImages, &stats.MultipleImages)

		db.QueryRow(`
			SELECT COUNT(*) FROM cafes
			WHERE json_extract(metadata, '$.businessStatus.status.code') = 2
		`).Scan(&stats.OpenNow)

		var minDate, maxDate sql.NullString
		db.QueryRow(`SELECT MIN(scraped_at), MAX(scraped_at) FROM cafes`).Scan(&minDate, &maxDate)
		if minDate.Valid {
			stats.MinScrapeDate = minDate.String
		}
		if maxDate.Valid {
			stats.MaxScrapeDate = maxDate.String
		}

		provRows, err := db.Query(`SELECT provider, COUNT(*) FROM cafes GROUP BY provider ORDER BY COUNT(*) DESC`)
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

	// ── GET /api/status ───────────────────────────────────────────────────────
	mux.HandleFunc("/api/status", func(w http.ResponseWriter, r *http.Request) {
		corsJSON(w)
		statusCacheMu.Lock()
		if time.Since(statusCachedAt) < statusCacheTTL && statusCacheBody != nil {
			body := statusCacheBody
			statusCacheMu.Unlock()
			w.Write(body)
			return
		}
		statusCacheMu.Unlock()

		resp := StatusResponse{}

		resp.Services = getCachedServices()

		now := time.Now().UTC()
		h1ago := now.Add(-1 * time.Hour).Format("2006-01-02 15:04:05")
		h24ago := now.Add(-24 * time.Hour).Format("2006-01-02 15:04:05")

		// Total counts
		db.QueryRow(`SELECT COUNT(*) FROM cafes`).Scan(&resp.TotalCafes)
		db.QueryRow(`SELECT COUNT(*) FROM images`).Scan(&resp.TotalImages)

		// Global time-window metrics
		db.QueryRow(`SELECT COUNT(*) FROM cafes WHERE scraped_at >= ?`, h1ago).Scan(&resp.CafesLastHour)
		db.QueryRow(`SELECT COUNT(*) FROM cafes WHERE scraped_at >= ?`, h24ago).Scan(&resp.Cafes24h)
		db.QueryRow(`SELECT COUNT(*) FROM images WHERE scraped_at >= ?`, h1ago).Scan(&resp.ImagesLastHour)
		db.QueryRow(`SELECT COUNT(*) FROM images WHERE scraped_at >= ?`, h24ago).Scan(&resp.Images24h)

		// Last activity timestamps
		var lastCafe, lastImage sql.NullString
		db.QueryRow(`SELECT MAX(scraped_at) FROM cafes`).Scan(&lastCafe)
		db.QueryRow(`SELECT MAX(scraped_at) FROM images`).Scan(&lastImage)
		if lastCafe.Valid {
			resp.LastCafeAt = lastCafe.String
		}
		if lastImage.Valid {
			resp.LastImageAt = lastImage.String
		}

		// Per-provider metrics
		providerRows, err := db.Query(`
			SELECT
				provider,
				COUNT(*) as total,
				SUM(CASE WHEN scraped_at >= ? THEN 1 ELSE 0 END) as last_hour,
				SUM(CASE WHEN scraped_at >= ? THEN 1 ELSE 0 END) as last_24h
			FROM cafes GROUP BY provider ORDER BY total DESC
		`, h1ago, h24ago)
		if err == nil {
			defer providerRows.Close()
			pmMap := map[string]*ProviderMetrics{}
			for providerRows.Next() {
				pm := &ProviderMetrics{}
				providerRows.Scan(&pm.Provider, &pm.Total, &pm.CafesLastHour, &pm.Cafes24h)
				pmMap[pm.Provider] = pm
				resp.PerProvider = append(resp.PerProvider, *pm)
			}
		}

		// Per-provider image metrics
		imgRows, err := db.Query(`
			SELECT
				provider,
				SUM(CASE WHEN scraped_at >= ? THEN 1 ELSE 0 END) as last_hour,
				SUM(CASE WHEN scraped_at >= ? THEN 1 ELSE 0 END) as last_24h
			FROM images GROUP BY provider
		`, h1ago, h24ago)
		if err == nil {
			defer imgRows.Close()
			for imgRows.Next() {
				var prov string
				var lh, l24 int
				imgRows.Scan(&prov, &lh, &l24)
				for i := range resp.PerProvider {
					if resp.PerProvider[i].Provider == prov {
						resp.PerProvider[i].ImagesLastHour = lh
						resp.PerProvider[i].Images24h = l24
					}
				}
			}
		}

		// Per-provider image distribution
		distRows, err := db.Query(`
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
		if err == nil {
			defer distRows.Close()
			for distRows.Next() {
				var prov string
				var cwi, c2, c10, c50, total int
				var avg float64
				distRows.Scan(&prov, &cwi, &c2, &c10, &c50, &avg, &total)
				for i := range resp.PerProvider {
					if resp.PerProvider[i].Provider == prov {
						resp.PerProvider[i].CafesWithImages = cwi
						resp.PerProvider[i].Cafes2Plus = c2
						resp.PerProvider[i].Cafes10Plus = c10
						resp.PerProvider[i].Cafes50Plus = c50
						resp.PerProvider[i].AvgImages = avg
						resp.PerProvider[i].TotalImages = total
					}
				}
			}
		}

		// Hourly stats for the last 24 hours (overall)
		hourlyMap := make(map[string]*HourlyStat)
		for i := 23; i >= 0; i-- {
			h := now.Add(-time.Duration(i) * time.Hour).Format("2006-01-02 15:00:00")
			hourlyMap[h] = &HourlyStat{Hour: h, Provider: "all"}
		}

		cafeHourlyRows, err := db.Query(`
			SELECT strftime('%Y-%m-%d %H:00:00', scraped_at) as hour, COUNT(*) 
			FROM cafes WHERE scraped_at >= ? GROUP BY hour
		`, h24ago)
		if err == nil {
			defer cafeHourlyRows.Close()
			for cafeHourlyRows.Next() {
				var h string
				var c int
				cafeHourlyRows.Scan(&h, &c)
				if stat, ok := hourlyMap[h]; ok {
					stat.Cafes = c
				}
			}
		}

		imageHourlyRows, err := db.Query(`
			SELECT strftime('%Y-%m-%d %H:00:00', scraped_at) as hour, COUNT(*) 
			FROM images WHERE scraped_at >= ? GROUP BY hour
		`, h24ago)
		if err == nil {
			defer imageHourlyRows.Close()
			for imageHourlyRows.Next() {
				var h string
				var c int
				imageHourlyRows.Scan(&h, &c)
				if stat, ok := hourlyMap[h]; ok {
					stat.Images = c
				}
			}
		}

		// Rebuild HourlyStats from the updated map, preserving order
		resp.HourlyStats = nil
		for i := 23; i >= 0; i-- {
			h := now.Add(-time.Duration(i) * time.Hour).Format("2006-01-02 15:00:00")
			resp.HourlyStats = append(resp.HourlyStats, *hourlyMap[h])
		}

		// Per-provider hourly stats
		providerHourlyMap := make(map[string]map[string]*HourlyStat)
		
		// Initialize for all providers we know about
		for _, pm := range resp.PerProvider {
			provMap := make(map[string]*HourlyStat)
			for i := 23; i >= 0; i-- {
				h := now.Add(-time.Duration(i) * time.Hour).Format("2006-01-02 15:00:00")
				provMap[h] = &HourlyStat{Hour: h, Provider: pm.Provider}
			}
			providerHourlyMap[pm.Provider] = provMap
		}

		provCafeHourlyRows, err := db.Query(`
			SELECT provider, strftime('%Y-%m-%d %H:00:00', scraped_at) as hour, COUNT(*) 
			FROM cafes WHERE scraped_at >= ? GROUP BY provider, hour
		`, h24ago)
		if err == nil {
			defer provCafeHourlyRows.Close()
			for provCafeHourlyRows.Next() {
				var p, h string
				var c int
				provCafeHourlyRows.Scan(&p, &h, &c)
				if provMap, ok := providerHourlyMap[p]; ok {
					if stat, ok := provMap[h]; ok {
						stat.Cafes = c
					}
				}
			}
		}

		provImageHourlyRows, err := db.Query(`
			SELECT provider, strftime('%Y-%m-%d %H:00:00', scraped_at) as hour, COUNT(*) 
			FROM images WHERE scraped_at >= ? GROUP BY provider, hour
		`, h24ago)
		if err == nil {
			defer provImageHourlyRows.Close()
			for provImageHourlyRows.Next() {
				var p, h string
				var c int
				provImageHourlyRows.Scan(&p, &h, &c)
				if provMap, ok := providerHourlyMap[p]; ok {
					if stat, ok := provMap[h]; ok {
						stat.Images = c
					}
				}
			}
		}

		// Append provider specific stats to the main list
		for _, pm := range resp.PerProvider {
			if provMap, ok := providerHourlyMap[pm.Provider]; ok {
				for i := 23; i >= 0; i-- {
					h := now.Add(-time.Duration(i) * time.Hour).Format("2006-01-02 15:00:00")
					resp.HourlyStats = append(resp.HourlyStats, *provMap[h])
				}
			}
		}

		resp.Disk = getDiskStats(dataDir)
		resp.DbQueue = getQueueStats(dataDir)

		body, err := json.Marshal(resp)
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		statusCacheMu.Lock()
		statusCacheBody = body
		statusCachedAt = time.Now()
		statusCacheMu.Unlock()

		w.Write(body)
	})

	// ── POST /api/services/{name}/{action} ────────────────────────────────────
	mux.HandleFunc("/api/services/", func(w http.ResponseWriter, r *http.Request) {
		corsJSON(w)
		if r.Method == "OPTIONS" {
			w.WriteHeader(204)
			return
		}
		if r.Method != "POST" {
			http.Error(w, "method not allowed", 405)
			return
		}

		// Parse /api/services/{name}/{action}
		parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/api/services/"), "/")
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

		// Return fresh state
		state, active := getServiceState(unit)
		result["state"] = state
		result["active"] = active

		json.NewEncoder(w).Encode(result)
	})

	scraperDir := os.Getenv("SCRAPER_DIR")
	if scraperDir == "" {
		scraperDir = "../scraper"
	}

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

	addr := ":8090"
	log.Printf("Serving on %s (DB: %s)", addr, dbPath)
	log.Fatal(http.ListenAndServe(addr, mux))
}
