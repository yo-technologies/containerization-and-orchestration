package main

import (
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"
)

type application struct {
	logger   *slog.Logger
	provider trace.TracerProvider
	tracer   trace.Tracer
	registry *prometheus.Registry
	requests *prometheus.CounterVec
	errors   *prometheus.CounterVec
	duration *prometheus.HistogramVec
	client   *http.Client
	selfURL  string
	loadSlot chan struct{}
}

func newApplication(provider trace.TracerProvider, logger *slog.Logger) *application {
	app := &application{
		logger: logger, provider: provider, tracer: provider.Tracer("lab-2/api"),
		registry: prometheus.NewRegistry(), loadSlot: make(chan struct{}, 1),
		requests: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "http_requests_total", Help: "Completed application requests, excluding metrics scrapes.",
		}, []string{"method", "route", "status"}),
		errors: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "http_errors_total", Help: "Application requests completed with HTTP 5xx.",
		}, []string{"route"}),
		duration: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name: "http_request_duration_seconds", Help: "Application request duration in seconds.",
			Buckets: []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 1.5, 2, 2.5, 3, 5, 10, 30},
		}, []string{"route"}),
	}
	app.registry.MustRegister(app.requests, app.errors, app.duration,
		collectors.NewGoCollector(), collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
	for _, route := range []string{"/health", "/fail", "/slow", "/load", "not_found"} {
		app.errors.WithLabelValues(route).Add(0)
	}
	app.client = &http.Client{
		Timeout: 5 * time.Second,
		Transport: otelhttp.NewTransport(http.DefaultTransport,
			otelhttp.WithTracerProvider(provider), otelhttp.WithPropagators(propagation.TraceContext{})),
	}
	return app
}

func (app *application) handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		_, _ = io.WriteString(w, "ok\n")
	})
	mux.HandleFunc("GET /fail", func(w http.ResponseWriter, r *http.Request) {
		span := trace.SpanFromContext(r.Context())
		span.RecordError(errors.New("intentional lab failure"))
		span.SetStatus(codes.Error, "intentional lab failure")
		http.Error(w, "intentional lab failure", http.StatusInternalServerError)
	})
	mux.HandleFunc("GET /slow", app.slow)
	mux.HandleFunc("GET /load", app.load)
	mux.Handle("GET /metrics", promhttp.HandlerFor(app.registry, promhttp.HandlerOpts{}))
	return otelhttp.NewHandler(app.observe(mux), "api",
		otelhttp.WithTracerProvider(app.provider),
		otelhttp.WithPropagators(propagation.TraceContext{}),
		otelhttp.WithSpanNameFormatter(func(_ string, r *http.Request) string {
			return r.Method + " " + routeLabel(r.URL.Path)
		}))
}

func (app *application) slow(w http.ResponseWriter, r *http.Request) {
	ctx, span := app.tracer.Start(r.Context(), "slow-op")
	defer span.End()
	timer := time.NewTimer(2 * time.Second)
	defer timer.Stop()
	select {
	case <-timer.C:
		_, _ = io.WriteString(w, "slow operation completed\n")
	case <-ctx.Done():
		span.RecordError(ctx.Err())
		span.SetStatus(codes.Error, "request canceled")
		http.Error(w, "request canceled", http.StatusRequestTimeout)
	}
}

func (app *application) load(w http.ResponseWriter, r *http.Request) {
	count := 100
	if raw := r.URL.Query().Get("requests"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 1 || parsed > 1000 {
			http.Error(w, "requests must be between 1 and 1000", http.StatusBadRequest)
			return
		}
		count = parsed
	}
	target := r.URL.Query().Get("target")
	if target == "" {
		target = "health"
	}
	if target != "health" && target != "fail" && target != "slow" {
		http.Error(w, "target must be health, fail, or slow", http.StatusBadRequest)
		return
	}
	select {
	case app.loadSlot <- struct{}{}:
		defer func() { <-app.loadSlot }()
	default:
		http.Error(w, "a load burst is already running", http.StatusTooManyRequests)
		return
	}

	var next, completed, serverErrors, transportErrors atomic.Int64
	var workers sync.WaitGroup
	for worker := 0; worker < min(10, count); worker++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for {
				if r.Context().Err() != nil || next.Add(1) > int64(count) {
					return
				}
				// The target is this listener, never an incoming Host header or URL.
				req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, app.selfURL+"/"+target, nil)
				if err != nil {
					transportErrors.Add(1)
					continue
				}
				response, err := app.client.Do(req)
				if err != nil {
					transportErrors.Add(1)
					continue
				}
				_, readErr := io.Copy(io.Discard, response.Body)
				_ = response.Body.Close()
				if readErr != nil {
					transportErrors.Add(1)
					continue
				}
				completed.Add(1)
				if response.StatusCode >= 500 {
					serverErrors.Add(1)
				}
			}
		}()
	}
	workers.Wait()
	w.Header().Set("Content-Type", "application/json")
	if transportErrors.Load() > 0 {
		w.WriteHeader(http.StatusBadGateway)
	}
	_ = json.NewEncoder(w).Encode(map[string]any{
		"requested": count, "target": target, "completed": completed.Load(),
		"server_errors": serverErrors.Load(), "transport_errors": transportErrors.Load(),
	})
}

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (w *statusWriter) WriteHeader(status int) {
	if w.status == 0 {
		w.status = status
		w.ResponseWriter.WriteHeader(status)
	}
}

func (w *statusWriter) Write(data []byte) (int, error) {
	if w.status == 0 {
		w.WriteHeader(http.StatusOK)
	}
	return w.ResponseWriter.Write(data)
}

func (w *statusWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }

func routeLabel(path string) string {
	switch path {
	case "/health", "/fail", "/slow", "/load", "/metrics":
		return path
	default:
		return "not_found"
	}
}

func (app *application) observe(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		started := time.Now()
		spanContext := trace.SpanContextFromContext(r.Context())
		w.Header().Set("X-Trace-ID", spanContext.TraceID().String())
		writer := &statusWriter{ResponseWriter: w}
		next.ServeHTTP(writer, r)
		if writer.status == 0 {
			writer.status = http.StatusOK
		}
		duration := time.Since(started).Seconds()
		route := routeLabel(r.URL.Path)
		if route != "/metrics" {
			app.requests.WithLabelValues(r.Method, route, strconv.Itoa(writer.status)).Inc()
			app.duration.WithLabelValues(route).Observe(duration)
			if writer.status >= 500 {
				app.errors.WithLabelValues(route).Inc()
			}
		}
		level := slog.LevelInfo
		if writer.status >= 500 {
			level = slog.LevelError
		}
		app.logger.Log(r.Context(), level, "request_completed",
			"method", r.Method, "route", route, "status", writer.status,
			"duration_seconds", duration,
			"trace_id", spanContext.TraceID().String(), "span_id", spanContext.SpanID().String())
	})
}
