package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"go.opentelemetry.io/otel/trace"
	collector "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	"google.golang.org/protobuf/proto"
)

type logBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *logBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *logBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

func testAPI(t *testing.T) (*api, *tracetest.InMemoryExporter, *logBuffer) {
	t.Helper()
	exporter := tracetest.NewInMemoryExporter()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSyncer(exporter), sdktrace.WithSampler(sdktrace.AlwaysSample()))
	logs := &logBuffer{}
	app := newAPI(slog.New(slog.NewJSONHandler(logs, nil)), provider, propagation.TraceContext{}, "")
	t.Cleanup(func() {
		app.client.CloseIdleConnections()
		_ = provider.Shutdown(context.Background())
	})
	return app, exporter, logs
}

func request(t *testing.T, handler http.Handler, path string) *httptest.ResponseRecorder {
	t.Helper()
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
	return w
}

func TestFailureMetricsLogsAndContext(t *testing.T) {
	a, exporter, logs := testAPI(t)
	h := a.handler()
	r := httptest.NewRequest("GET", "/fail", nil)
	r.Header.Set("traceparent", "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 500 || w.Header().Get("X-Trace-ID") != "0123456789abcdef0123456789abcdef" {
		t.Fatalf("failure response: %d, %v", w.Code, w.Header())
	}
	spans := exporter.GetSpans()
	if len(spans) != 1 || spans[0].Status.Code != codes.Error || len(spans[0].Events) == 0 {
		t.Fatalf("expected failed span with exception: %+v", spans)
	}
	var log map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(logs.String())), &log); err != nil {
		t.Fatal(err)
	}
	if log["level"] != "ERROR" || log["trace_id"] != spans[0].SpanContext.TraceID().String() {
		t.Fatalf("log/trace mismatch: %v", log)
	}
	if health := request(t, h, "/health"); health.Code != 200 || health.Body.String() != "ok" {
		t.Fatalf("health response: %d %q", health.Code, health.Body.String())
	}
	metrics := request(t, h, "/metrics").Body.String()
	for _, want := range []string{
		`http_requests_total{code="500",method="GET",route="/fail"} 1`,
		`http_errors_total{code="500",method="GET",route="/fail"} 1`,
		`http_request_duration_seconds_count{code="500",method="GET",route="/fail"} 1`,
		`http_requests_total{code="200",method="GET",route="/health"} 1`,
	} {
		if !strings.Contains(metrics, want) {
			t.Errorf("missing metric %s", want)
		}
	}
	if strings.Contains(metrics, `route="/metrics"`) || len(exporter.GetSpans()) != 2 {
		t.Fatal("scrapes affected application telemetry")
	}
}

func TestSlowNestedSpan(t *testing.T) {
	a, exporter, _ := testAPI(t)
	start := time.Now()
	w := request(t, a.handler(), "/slow")
	if w.Code != 200 || time.Since(start) < time.Second {
		t.Fatalf("slow response: %d", w.Code)
	}
	var body struct {
		SleptMS int64 `json:"slept_ms"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body.SleptMS < 1000 || body.SleptMS > 3000 {
		t.Fatalf("delay %d ms", body.SleptMS)
	}
	spans := exporter.GetSpans()
	if len(spans) != 2 {
		t.Fatalf("expected two spans, got %d", len(spans))
	}
	child, parent := spans[0], spans[1]
	if child.Name != "slow-op" || child.Parent.SpanID() != parent.SpanContext.SpanID() ||
		child.SpanContext.TraceID() != parent.SpanContext.TraceID() || child.EndTime.Sub(child.StartTime) < time.Second {
		t.Fatalf("invalid nested slow span: %+v", child)
	}
}

func TestLoadMakesRealRequestsAndPropagatesTrace(t *testing.T) {
	a, exporter, _ := testAPI(t)
	srv := httptest.NewUnstartedServer(a.handler())
	// Set before serving to keep this field immutable during requests.
	a.selfURL = "http://" + srv.Listener.Addr().String()
	srv.Start()
	defer srv.Close()
	// Dispatch the parent synchronously; the burst still crosses real HTTP.
	resp := request(t, srv.Config.Handler, "/load?count=24&concurrency=4&target=fail")
	var result struct{ Requested, Completed, Failed int }
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		t.Fatal(err)
	}
	if resp.Code != 200 || result.Requested != 24 || result.Completed != 24 || result.Failed != 24 {
		t.Fatalf("burst: status=%d %+v", resp.Code, result)
	}
	spans := exporter.GetSpans()
	if len(spans) != 49 {
		t.Fatalf("expected 1 parent + 24 client + 24 server spans, got %d", len(spans))
	}
	traceID := resp.Header().Get("X-Trace-ID")
	serverRequests := 0
	for _, span := range spans {
		if span.SpanContext.TraceID().String() != traceID {
			t.Fatal("load did not propagate trace context")
		}
		if span.SpanKind == trace.SpanKindServer && span.Name == "GET /fail" {
			serverRequests++
		}
	}
	if serverRequests != 24 {
		t.Fatalf("got %d self requests", serverRequests)
	}
	defaultBurst := request(t, srv.Config.Handler, "/load")
	if err := json.NewDecoder(defaultBurst.Body).Decode(&result); err != nil {
		t.Fatal(err)
	}
	if defaultBurst.Code != 200 || result.Requested != 100 || result.Completed != 100 || result.Failed != 0 {
		t.Fatalf("default burst: status=%d %+v", defaultBurst.Code, result)
	}
}

func TestLoadValidationAndExclusion(t *testing.T) {
	a, _, _ := testAPI(t)
	h := a.handler()
	for _, path := range []string{"/load?count=0", "/load?count=1001", "/load?concurrency=51", "/load?target=load", "/load?count=no"} {
		if w := request(t, h, path); w.Code != 400 {
			t.Errorf("%s: status=%d", path, w.Code)
		}
	}
	a.loadMu.Lock()
	w := request(t, h, "/load")
	a.loadMu.Unlock()
	if w.Code != 429 {
		t.Fatalf("concurrent burst status=%d", w.Code)
	}
}

func TestOTLPHTTPExport(t *testing.T) {
	payloads := make(chan *collector.ExportTraceServiceRequest, 1)
	receiver := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/traces" || r.Header.Get("Content-Type") != "application/x-protobuf" {
			t.Errorf("unexpected OTLP request: %s %v", r.URL.Path, r.Header)
		}
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
			w.WriteHeader(400)
			return
		}
		var payload collector.ExportTraceServiceRequest
		if err := proto.Unmarshal(body, &payload); err != nil {
			t.Error(err)
			w.WriteHeader(400)
			return
		}
		payloads <- &payload
		w.Header().Set("Content-Type", "application/x-protobuf")
		w.WriteHeader(200)
	}))
	defer receiver.Close()
	t.Setenv("OTEL_EXPORTER_OTLP_ENDPOINT", receiver.URL)
	t.Setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")
	exporter, err := otlptracehttp.New(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	provider := sdktrace.NewTracerProvider(sdktrace.WithSyncer(exporter))
	defer provider.Shutdown(context.Background())
	a := newAPI(slog.New(slog.NewJSONHandler(io.Discard, nil)), provider, propagation.TraceContext{}, "")
	defer a.client.CloseIdleConnections()
	w := request(t, a.handler(), "/fail")
	select {
	case p := <-payloads:
		if len(p.ResourceSpans) != 1 || len(p.ResourceSpans[0].ScopeSpans) != 1 {
			t.Fatalf("invalid OTLP payload: %v", p)
		}
		spans := p.ResourceSpans[0].ScopeSpans[0].Spans
		if len(spans) != 1 || spans[0].Name != "GET /fail" || spans[0].Status.Code.String() != "STATUS_CODE_ERROR" ||
			w.Header().Get("X-Trace-ID") != spansTraceID(spans[0].TraceId) {
			t.Fatalf("invalid exported span: %v", spans)
		}
	case <-time.After(time.Second):
		t.Fatal("no OTLP payload received")
	}
}

func spansTraceID(b []byte) string {
	var id trace.TraceID
	copy(id[:], b)
	return id.String()
}
