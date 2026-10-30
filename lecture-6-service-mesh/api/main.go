package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math/rand/v2"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var version = "dev"
var orderFail bool
var flaky bool

type application struct {
	db        *sql.DB
	role      string
	fail      bool
	ready     atomic.Bool
	registry  *prometheus.Registry
	requests  *prometheus.CounterVec
	duration  *prometheus.HistogramVec
	created   prometheus.Counter
	processed prometheus.Counter
	workError prometheus.Counter
	pending   prometheus.Gauge
	dbUp      prometheus.Gauge
}

func newApplication(db *sql.DB, role string, fail bool) *application {
	a := &application{
		db: db, role: role, fail: fail, registry: prometheus.NewRegistry(),
		requests: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "shop_http_requests_total", Help: "HTTP requests, excluding metrics scrapes.",
		}, []string{"method", "route", "code"}),
		duration: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name: "shop_http_request_duration_seconds", Help: "HTTP request duration.",
			Buckets: prometheus.DefBuckets,
		}, []string{"route"}),
		created: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "shop_orders_created_total", Help: "Orders inserted by this process.",
		}),
		processed: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "shop_worker_processed_orders_total", Help: "Orders processed by this worker.",
		}),
		workError: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "shop_worker_errors_total", Help: "Failed worker database operations.",
		}),
		pending: prometheus.NewGauge(prometheus.GaugeOpts{
			Name: "shop_pending_orders", Help: "Database-wide pending orders, sampled by this worker; do not sum replicas.",
		}),
		dbUp: prometheus.NewGauge(prometheus.GaugeOpts{
			Name: "shop_database_up", Help: "Whether the most recent database operation succeeded.",
		}),
	}
	a.registry.MustRegister(a.requests, a.duration, a.created, a.processed, a.workError, a.dbUp)
	if role == "worker" {
		a.registry.MustRegister(a.pending)
	}
	a.registry.MustRegister(prometheus.NewGaugeFunc(prometheus.GaugeOpts{
		Name: "shop_schema_ready", Help: "Whether schema initialization succeeded at least once.",
	}, func() float64 {
		if a.ready.Load() {
			return 1
		}
		return 0
	}))
	build := prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Name: "shop_build_info", Help: "Application build and role.",
	}, []string{"version", "role"})
	build.WithLabelValues(version, role).Set(1)
	a.registry.MustRegister(build, prometheus.NewGoCollector(), prometheus.NewProcessCollector(prometheus.ProcessCollectorOpts{}))
	return a
}

func main() {
	if len(os.Args) == 3 && os.Args[1] == "probe" {
		response, err := http.Get(os.Args[2])
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		defer response.Body.Close()
		fmt.Println("HTTP", response.StatusCode)
		_, _ = io.Copy(os.Stdout, response.Body)
		return
	}
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	if err := run(); err != nil {
		slog.Error("service stopped", "error", err)
		os.Exit(1)
	}
}

func run() error {
	if v := os.Getenv("VERSION"); v != "" {
		version = v
	}
	orderFail = os.Getenv("FAIL") == "true"
	flaky = os.Getenv("FLAKY") == "true"
	role := os.Getenv("APP_ROLE")
	if role == "" {
		role = "api"
	}
	if role != "api" && role != "worker" {
		return errors.New("APP_ROLE must be api or worker")
	}
	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" {
		return errors.New("DATABASE_URL is required")
	}
	fail := false
	if raw := os.Getenv("HEALTH_FAIL"); raw != "" {
		var err error
		fail, err = strconv.ParseBool(raw)
		if err != nil {
			return errors.New("HEALTH_FAIL must be a boolean")
		}
	}
	interval := time.Second
	if raw := os.Getenv("POLL_INTERVAL"); raw != "" {
		var err error
		interval, err = time.ParseDuration(raw)
		if err != nil || interval < 10*time.Millisecond {
			return errors.New("POLL_INTERVAL must be a duration >= 10ms")
		}
	}
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		return errors.New("invalid DATABASE_URL")
	}
	defer db.Close()
	db.SetMaxOpenConns(5)
	db.SetMaxIdleConns(2)
	db.SetConnMaxLifetime(5 * time.Minute)
	a := newApplication(db, role, fail)
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	var tasks sync.WaitGroup
	tasks.Add(1)
	go func() {
		defer tasks.Done()
		a.background(ctx, interval)
	}()
	address := os.Getenv("LISTEN_ADDR")
	if address == "" {
		address = ":8080"
	}
	server := &http.Server{
		Addr: address, Handler: a.handler(), ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout: 10 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second,
	}
	errorsCh := make(chan error, 1)
	go func() { errorsCh <- server.ListenAndServe() }()
	slog.Info("service started", "role", role, "version", version, "address", address)
	var serveErr error
	select {
	case <-ctx.Done():
	case serveErr = <-errorsCh:
	}
	cancel()
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer shutdownCancel()
	if err := server.Shutdown(shutdownCtx); err != nil {
		_ = server.Close()
	}
	tasks.Wait()
	if serveErr != nil && !errors.Is(serveErr, http.ErrServerClosed) {
		return serveErr
	}
	return nil
}

func (a *application) background(ctx context.Context, interval time.Duration) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		if !a.ready.Load() {
			opCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
			err := a.initialize(opCtx)
			cancel()
			if err == nil {
				a.ready.Store(true)
				a.dbUp.Set(1)
				slog.Info("database schema ready", "role", a.role)
			} else if ctx.Err() == nil {
				a.dbUp.Set(0)
				slog.Warn("waiting for database", "role", a.role)
			}
		}
		if a.role == "worker" && a.ready.Load() {
			a.processBatch(ctx)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (a *application) initialize(ctx context.Context) error {
	tx, err := a.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	// Serialize first startup across API and worker replicas, including catalog DDL.
	if _, err := tx.ExecContext(ctx, "SELECT pg_advisory_xact_lock(731982001)"); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx, `CREATE TABLE IF NOT EXISTS orders (
		id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
		item TEXT NOT NULL,
		quantity INTEGER NOT NULL CHECK (quantity > 0),
		processed BOOLEAN NOT NULL DEFAULT false,
		created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
		processed_at TIMESTAMPTZ
	)`); err != nil {
		return err
	}
	return tx.Commit()
}

func (a *application) processBatch(ctx context.Context) {
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	result, err := a.db.ExecContext(ctx, `WITH batch AS (
		SELECT id FROM orders WHERE NOT processed ORDER BY id LIMIT 20 FOR UPDATE SKIP LOCKED
	) UPDATE orders SET processed = true, processed_at = now()
	FROM batch WHERE orders.id = batch.id`)
	if err != nil {
		a.dbUp.Set(0)
		a.workError.Inc()
		if ctx.Err() == nil {
			slog.Warn("worker database operation failed")
		}
		return
	}
	count, _ := result.RowsAffected()
	a.processed.Add(float64(count))
	if count > 0 {
		slog.Info("orders processed", "count", count)
	}
	var pending int
	if err := a.db.QueryRowContext(ctx, "SELECT count(*) FROM orders WHERE NOT processed").Scan(&pending); err != nil {
		a.dbUp.Set(0)
		a.workError.Inc()
		return
	}
	a.dbUp.Set(1)
	a.pending.Set(float64(pending))
}

func (a *application) handler() http.Handler {
	mux := http.NewServeMux()
	mux.Handle("GET /metrics", promhttp.HandlerFor(a.registry, promhttp.HandlerOpts{}))
	mux.HandleFunc("GET /health", a.instrument("/health", func(w http.ResponseWriter, r *http.Request) {
		if a.fail {
			http.Error(w, "deliberate health failure", http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		_, _ = io.WriteString(w, "ok\n")
	}))
	mux.HandleFunc("GET /version", a.instrument("/version", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"version": version, "role": a.role})
	}))
	if a.role == "api" {
		mux.HandleFunc("POST /order", a.instrument("/order", a.createOrder))
		mux.HandleFunc("GET /orders", a.instrument("/orders", a.listOrders))
	}
	return mux
}

type responseRecorder struct {
	http.ResponseWriter
	status int
}

func (w *responseRecorder) WriteHeader(code int) {
	if w.status == 0 {
		w.status = code
		w.ResponseWriter.WriteHeader(code)
	}
}

func (w *responseRecorder) Write(b []byte) (int, error) {
	if w.status == 0 {
		w.WriteHeader(http.StatusOK)
	}
	return w.ResponseWriter.Write(b)
}

func (a *application) instrument(route string, next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		w.Header().Set("X-Shop-Version", version)
		recorder := &responseRecorder{ResponseWriter: w}
		next(recorder, r)
		code := recorder.status
		if code == 0 {
			code = http.StatusOK
		}
		a.requests.WithLabelValues(r.Method, route, strconv.Itoa(code)).Inc()
		a.duration.WithLabelValues(route).Observe(time.Since(start).Seconds())
	}
}

type order struct {
	ID          int64      `json:"id"`
	Item        string     `json:"item"`
	Quantity    int        `json:"quantity"`
	Processed   bool       `json:"processed"`
	CreatedAt   time.Time  `json:"created_at"`
	ProcessedAt *time.Time `json:"processed_at"`
}

func (a *application) createOrder(w http.ResponseWriter, r *http.Request) {
	// Deliberate failures happen before the database write. Retrying these 500s cannot duplicate an order.
	if (version == "v2" && orderFail) || (version == "v1" && flaky && rand.Float64() < 0.30) {
		http.Error(w, "intentional failure before INSERT", http.StatusInternalServerError)
		return
	}
	var input struct {
		Item     string `json:"item"`
		Quantity int    `json:"quantity"`
	}
	r.Body = http.MaxBytesReader(w, r.Body, 8192)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		http.Error(w, "expected JSON: {\"item\":\"book\",\"quantity\":1}", http.StatusBadRequest)
		return
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		http.Error(w, "expected one JSON object", http.StatusBadRequest)
		return
	}
	input.Item = strings.TrimSpace(input.Item)
	if input.Item == "" || len(input.Item) > 200 || input.Quantity < 1 || input.Quantity > 1000 {
		http.Error(w, "item must be 1..200 bytes; quantity must be 1..1000", http.StatusBadRequest)
		return
	}
	if !a.ready.Load() {
		http.Error(w, "database is initializing", http.StatusServiceUnavailable)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	var out order
	err := a.db.QueryRowContext(ctx, `INSERT INTO orders (item, quantity) VALUES ($1, $2)
		RETURNING id, item, quantity, processed, created_at, processed_at`, input.Item, input.Quantity).
		Scan(&out.ID, &out.Item, &out.Quantity, &out.Processed, &out.CreatedAt, &out.ProcessedAt)
	if err != nil {
		a.dbUp.Set(0)
		http.Error(w, "database unavailable", http.StatusServiceUnavailable)
		return
	}
	a.dbUp.Set(1)
	a.created.Inc()
	writeJSON(w, http.StatusCreated, out)
}

func (a *application) listOrders(w http.ResponseWriter, r *http.Request) {
	if !a.ready.Load() {
		http.Error(w, "database is initializing", http.StatusServiceUnavailable)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	rows, err := a.db.QueryContext(ctx, `SELECT id, item, quantity, processed, created_at, processed_at
		FROM orders ORDER BY id DESC LIMIT 100`)
	if err != nil {
		a.dbUp.Set(0)
		http.Error(w, "database unavailable", http.StatusServiceUnavailable)
		return
	}
	defer rows.Close()
	orders := make([]order, 0)
	for rows.Next() {
		var out order
		if err := rows.Scan(&out.ID, &out.Item, &out.Quantity, &out.Processed, &out.CreatedAt, &out.ProcessedAt); err != nil {
			http.Error(w, "database read failed", http.StatusServiceUnavailable)
			return
		}
		orders = append(orders, out)
	}
	if err := rows.Err(); err != nil {
		a.dbUp.Set(0)
		http.Error(w, "database read failed", http.StatusServiceUnavailable)
		return
	}
	a.dbUp.Set(1)
	writeJSON(w, http.StatusOK, map[string]any{"orders": orders})
}

func writeJSON(w http.ResponseWriter, code int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	if err := json.NewEncoder(w).Encode(value); err != nil {
		slog.Warn("response write failed", "error", fmt.Sprintf("%T", err))
	}
}
