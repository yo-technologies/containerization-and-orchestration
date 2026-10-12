package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"math/rand/v2"
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

type api struct {
	logger     *slog.Logger
	provider   trace.TracerProvider
	propagator propagation.TextMapPropagator
	tracer     trace.Tracer
	registry   *prometheus.Registry
	requests   *prometheus.CounterVec
	errors     *prometheus.CounterVec
	duration   *prometheus.HistogramVec
	client     *http.Client
	selfURL    string
	loadMu     sync.Mutex
}

func newAPI(logger *slog.Logger, provider trace.TracerProvider, propagator propagation.TextMapPropagator, selfURL string) *api {
	a := &api{
		logger: logger, provider: provider, propagator: propagator,
		tracer: provider.Tracer("lab-api"), registry: prometheus.NewRegistry(), selfURL: selfURL,
		requests: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "http_requests_total", Help: "Completed application HTTP requests."}, []string{"method", "route", "code"}),
		errors: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "http_errors_total", Help: "Completed application HTTP requests with 5xx status."}, []string{"method", "route", "code"}),
		duration: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name: "http_request_duration_seconds", Help: "Application HTTP request duration in seconds.",
			Buckets: []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 1.5, 2, 2.5, 3, 5, 10}}, []string{"method", "route", "code"}),
		client: &http.Client{Timeout: 5 * time.Second, Transport: otelhttp.NewTransport(http.DefaultTransport.(*http.Transport).Clone(),
			otelhttp.WithTracerProvider(provider), otelhttp.WithPropagators(propagator))},
	}
	a.registry.MustRegister(a.requests, a.errors, a.duration, collectors.NewGoCollector(), collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
	return a
}

func (a *api) handler() http.Handler {
	mux := http.NewServeMux()
	mux.Handle("/health", a.observe("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		_, _ = io.WriteString(w, "ok")
	}))
	mux.Handle("/fail", a.observe("/fail", func(w http.ResponseWriter, r *http.Request) {
		span := trace.SpanFromContext(r.Context())
		span.RecordError(errors.New("intentional failure"))
		span.SetStatus(codes.Error, "intentional failure")
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "intentional failure"})
	}))
	mux.Handle("/slow", a.observe("/slow", a.slow))
	mux.Handle("/load", a.observe("/load", a.load))
	// Scrapes must not inflate application RPS or add a stream of traces.
	mux.Handle("GET /metrics", promhttp.HandlerFor(a.registry, promhttp.HandlerOpts{}))
	mux.Handle("/", a.observe("unmatched", func(w http.ResponseWriter, r *http.Request) {
		http.NotFound(w, r)
	}))
	return mux
}

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (w *statusWriter) WriteHeader(code int) {
	if w.status != 0 {
		return
	}
	w.status = code
	w.ResponseWriter.WriteHeader(code)
}

func (w *statusWriter) Write(b []byte) (int, error) {
	if w.status == 0 {
		w.WriteHeader(http.StatusOK)
	}
	return w.ResponseWriter.Write(b)
}

func (w *statusWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }

func (a *api) observe(route string, next http.HandlerFunc) http.Handler {
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		span := trace.SpanFromContext(r.Context()).SpanContext()
		w.Header().Set("X-Trace-ID", span.TraceID().String())
		sw := &statusWriter{ResponseWriter: w}
		if r.Method != http.MethodGet {
			w.Header().Set("Allow", http.MethodGet)
			writeJSON(sw, http.StatusMethodNotAllowed, map[string]string{"error": "use GET"})
		} else {
			next(sw, r)
		}
		if sw.status == 0 {
			sw.WriteHeader(http.StatusOK)
		}
		elapsed := time.Since(start).Seconds()
		method := r.Method
		// Avoid unbounded label cardinality from arbitrary HTTP methods.
		switch method {
		case "GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS":
		default:
			method = "OTHER"
		}
		code := strconv.Itoa(sw.status)
		a.requests.WithLabelValues(method, route, code).Inc()
		a.duration.WithLabelValues(method, route, code).Observe(elapsed)
		level := slog.LevelInfo
		if sw.status >= 500 {
			a.errors.WithLabelValues(method, route, code).Inc()
			level = slog.LevelError
		}
		a.logger.Log(r.Context(), level, "http request", "method", r.Method, "route", route,
			"status", sw.status, "duration_seconds", elapsed,
			"trace_id", span.TraceID().String(), "span_id", span.SpanID().String())
	})
	return otelhttp.NewHandler(h, "GET "+route, otelhttp.WithTracerProvider(a.provider),
		otelhttp.WithPropagators(a.propagator), otelhttp.WithSpanNameFormatter(func(_ string, r *http.Request) string {
			return r.Method + " " + route
		}))
}

func (a *api) slow(w http.ResponseWriter, r *http.Request) {
	_, span := a.tracer.Start(r.Context(), "slow-op")
	defer span.End()
	delay := time.Duration(1000+rand.IntN(2001)) * time.Millisecond
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-timer.C:
		writeJSON(w, http.StatusOK, map[string]int64{"slept_ms": delay.Milliseconds()})
	case <-r.Context().Done():
		span.RecordError(r.Context().Err())
		span.SetStatus(codes.Error, "request cancelled")
		writeJSON(w, http.StatusRequestTimeout, map[string]string{"error": "request cancelled"})
	}
}

func (a *api) load(w http.ResponseWriter, r *http.Request) {
	count, err := boundedInt(r, "count", 100, 1, 1000)
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	concurrency, err := boundedInt(r, "concurrency", 10, 1, 50)
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	target := r.URL.Query().Get("target")
	if target == "" {
		target = "health"
	}
	if target != "health" && target != "fail" {
		writeJSON(w, 400, map[string]string{"error": "target must be health or fail"})
		return
	}
	// One burst at a time; never allow recursive calls to /load.
	if !a.loadMu.TryLock() {
		writeJSON(w, 429, map[string]string{"error": "a load burst is already running"})
		return
	}
	defer a.loadMu.Unlock()
	ctx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
	defer cancel()
	var cursor, completed, failed atomic.Int64
	var workers sync.WaitGroup
	for i := 0; i < min(concurrency, count); i++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for {
				if ctx.Err() != nil || cursor.Add(1) > int64(count) {
					return
				}
				req, err := http.NewRequestWithContext(ctx, http.MethodGet, a.selfURL+"/"+target, nil)
				if err != nil {
					failed.Add(1)
					continue
				}
				resp, err := a.client.Do(req)
				if err != nil {
					failed.Add(1)
					continue
				}
				_, readErr := io.Copy(io.Discard, resp.Body)
				_ = resp.Body.Close()
				completed.Add(1)
				if resp.StatusCode >= 400 || readErr != nil {
					failed.Add(1)
				}
			}
		}()
	}
	workers.Wait()
	status := http.StatusOK
	if ctx.Err() != nil {
		status = http.StatusGatewayTimeout
		span := trace.SpanFromContext(r.Context())
		span.RecordError(ctx.Err())
		span.SetStatus(codes.Error, "load burst timed out or cancelled")
	}
	writeJSON(w, status, map[string]any{"requested": count, "completed": completed.Load(), "failed": failed.Load(), "target": target})
}

func boundedInt(r *http.Request, name string, fallback, low, high int) (int, error) {
	value := r.URL.Query().Get(name)
	if value == "" {
		return fallback, nil
	}
	n, err := strconv.Atoi(value)
	if err != nil || n < low || n > high {
		return 0, errors.New(name + " must be between " + strconv.Itoa(low) + " and " + strconv.Itoa(high))
	}
	return n, nil
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}
