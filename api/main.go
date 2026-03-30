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
	"strings"
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
	Name    string `json:"name"`
	Unit    string `json:"unit"`
	State   string `json:"state"`
	Active  bool   `json:"active"`
}

type ProviderMetrics struct {
	Provider      string `json:"provider"`
	CafesLastHour int    `json:"cafes_last_hour"`
	Cafes24h      int    `json:"cafes_24h"`
	ImagesLastHour int   `json:"images_last_hour"`
	Images24h     int    `json:"images_24h"`
	Total         int    `json:"total"`
}

type DiskStats struct {
	DataDirGB  float64 `json:"data_dir_gb"`
	LimitGB    float64 `json:"limit_gb"`
	UsedPct    float64 `json:"used_pct"`
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
	Services       []ServiceStatus        `json:"services"`
	PerProvider    []ProviderMetrics      `json:"per_provider"`
	TotalCafes     int                    `json:"total_cafes"`
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
	"kakao":        "workcafe-scraper-kakao",
	"google":       "workcafe-scraper-google",
	"osm":          "workcafe-scraper-osm",
	"naver":        "workcafe-scraper-naver",
	"imagescraper": "workcafe-kakao-images",
	"api":          "workcafe-api",
	"frontend":     "workcafe-frontend",
}

var serviceOrder = []string{"kakao", "google", "osm", "naver", "imagescraper", "api", "frontend"}

func getServiceState(unit string) (string, bool) {
	out, err := exec.Command("systemctl", "--user", "is-active", unit).Output()
	state := strings.TrimSpace(string(out))
	if err != nil || state == "" {
		state = "inactive"
	}
	return state, state == "active"
}

func getDiskStats(dataDir string) DiskStats {
	const limitGB = 40.0
	out, err := exec.Command("du", "-sb", dataDir).Output()
	if err != nil {
		return DiskStats{LimitGB: limitGB}
	}
	var bytes int64
	fmt.Sscanf(string(out), "%d", &bytes)
	gb := float64(bytes) / (1024 * 1024 * 1024)
	return DiskStats{
		DataDirGB: math_round2(gb),
		LimitGB:   limitGB,
		UsedPct:   math_round2(gb / limitGB * 100),
	}
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
		rows, err := db.Query(`SELECT id, provider, provider_id, name, lat, lon, COALESCE(address,''), COALESCE(url,''), COALESCE(metadata,'null'), COALESCE(scraped_at,'') FROM cafes`)
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		defer rows.Close()

		cafes := make([]Cafe, 0, 512)
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
		json.NewEncoder(w).Encode(cafes)
	})

	// ── GET /api/status ───────────────────────────────────────────────────────
	mux.HandleFunc("/api/status", func(w http.ResponseWriter, r *http.Request) {
		resp := StatusResponse{}

		// Service states
		for _, name := range serviceOrder {
			unit := serviceMap[name]
			state, active := getServiceState(unit)
			resp.Services = append(resp.Services, ServiceStatus{
				Name:   name,
				Unit:   unit,
				State:  state,
				Active: active,
			})
		}

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

		corsJSON(w)
		json.NewEncoder(w).Encode(resp)
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

	addr := ":8090"
	log.Printf("Serving on %s (DB: %s)", addr, dbPath)
	log.Fatal(http.ListenAndServe(addr, mux))
}
