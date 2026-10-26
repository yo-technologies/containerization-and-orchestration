package main

import (
	"bytes"
	"context"
	"encoding/hex"
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
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"go.opentelemetry.io/otel/trace"
	collectortrace "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	traceproto "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"
)

type logBuffer struct {
	mu     sync.Mutex
	buffer bytes.Buffer
}

func (b *logBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buffer.Write(p)
}

func (b *logBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buffer.String()
}

func fixture(t *testing.T) (*application, *tracetest.InMemoryExporter, *logBuffer) {
	t.Helper()
	exporter := tracetest.NewInMemoryExporter()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSyncer(exporter), sdktrace.WithSampler(sdktrace.AlwaysSample()))
	t.Cleanup(func() { _ = provider.Shutdown(context.Background()) })
	logs := &logBuffer{}
	app := newApplication(provider, slog.New(slog.NewJSONHandler(logs, nil)))
	return app, exporter, logs
}

func request(handler http.Handler, path string) *httptest.ResponseRecorder {
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, path, nil))
	return response
}

func TestHealthFailureMetricsAndLogTraceCorrelation(t *testing.T) {
	app, exporter, logs := fixture(t)
	handler := app.handler()
	if response := request(handler, "/health"); response.Code != 200 || response.Body.String() != "ok\n" {
		t.Fatalf("health: %d %q", response.Code, response.Body.String())
	}
	failure := request(handler, "/fail")
	if failure.Code != 500 {
		t.Fatalf("fail: %d", failure.Code)
	}
	spans := exporter.GetSpans()
	if len(spans) != 2 || spans[1].Status.Code != codes.Error || len(spans[1].Events) == 0 {
		t.Fatalf("expected health and failed server spans with an exception: %+v", spans)
	}
	traceID := failure.Header().Get("X-Trace-ID")
	if traceID != spans[1].SpanContext.TraceID().String() || !spans[1].SpanContext.IsValid() {
		t.Fatal("response trace ID does not match exported failure")
	}
	var failLog map[string]any
	for _, line := range strings.Split(strings.TrimSpace(logs.String()), "\n") {
		var entry map[string]any
		if err := json.Unmarshal([]byte(line), &entry); err != nil {
			t.Fatal(err)
		}
		if entry["route"] == "/fail" {
			failLog = entry
		}
	}
	if failLog["trace_id"] != traceID || failLog["level"] != "ERROR" || failLog["status"] != float64(500) {
		t.Fatalf("failed request JSON log mismatch: %+v", failLog)
	}
	metrics := request(handler, "/metrics").Body.String()
	for _, expected := range []string{
		`http_requests_total{method="GET",route="/health",status="200"} 1`,
		`http_requests_total{method="GET",route="/fail",status="500"} 1`,
		`http_errors_total{route="/fail"} 1`,
		`http_request_duration_seconds_count{route="/fail"} 1`,
		`http_request_duration_seconds_bucket{route="/health",le="+Inf"} 1`,
	} {
		if !strings.Contains(metrics, expected) {
			t.Errorf("missing metric: %s", expected)
		}
	}
	if strings.Contains(metrics, `route="/metrics"`) {
		t.Fatal("scrapes must not pollute application RED metrics")
	}
}

func TestSlowNestedSpanAndIncomingTracePropagation(t *testing.T) {
	app, exporter, _ := fixture(t)
	req := httptest.NewRequest(http.MethodGet, "/slow", nil)
	req.Header.Set("traceparent", "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01")
	response := httptest.NewRecorder()
	app.handler().ServeHTTP(response, req)
	if response.Code != 200 {
		t.Fatalf("slow: %d", response.Code)
	}
	var parent, child tracetest.SpanStub
	for _, span := range exporter.GetSpans() {
		if span.Name == "slow-op" {
			child = span
		} else if span.SpanKind == trace.SpanKindServer {
			parent = span
		}
	}
	if !child.SpanContext.IsValid() || child.Parent.SpanID() != parent.SpanContext.SpanID() {
		t.Fatal("slow-op is not a child of the incoming server span")
	}
	if parent.SpanContext.TraceID().String() != "0123456789abcdef0123456789abcdef" ||
		child.SpanContext.TraceID() != parent.SpanContext.TraceID() || !parent.Parent.IsRemote() {
		t.Fatal("incoming traceparent was not propagated")
	}
	if duration := child.EndTime.Sub(child.StartTime); duration < 1900*time.Millisecond || duration > 4*time.Second {
		t.Fatalf("unexpected slow operation duration: %s", duration)
	}
}

func TestLoadActuallyCallsItselfAndPropagatesTrace(t *testing.T) {
	app, exporter, _ := fixture(t)
	// Finish URL configuration before Serve starts to avoid mutable runtime state.
	server := httptest.NewUnstartedServer(app.handler())
	app.selfURL = "http://" + server.Listener.Addr().String()
	server.Start()
	defer server.Close()
	response, err := server.Client().Get(server.URL + "/load?requests=12&target=fail")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	var result struct {
		Completed       int `json:"completed"`
		ServerErrors    int `json:"server_errors"`
		TransportErrors int `json:"transport_errors"`
	}
	if err := json.NewDecoder(response.Body).Decode(&result); err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != 200 || result.Completed != 12 || result.ServerErrors != 12 || result.TransportErrors != 0 {
		t.Fatalf("load status=%d result=%+v", response.StatusCode, result)
	}
	traceID := response.Header.Get("X-Trace-ID")
	var serverSpans, clientSpans int
	for _, span := range exporter.GetSpans() {
		if span.SpanContext.TraceID().String() != traceID {
			t.Fatal("self-request broke the parent trace")
		}
		if span.SpanKind == trace.SpanKindServer && span.Name == "GET /fail" {
			serverSpans++
		}
		if span.SpanKind == trace.SpanKindClient {
			clientSpans++
		}
	}
	if serverSpans != 12 || clientSpans != 12 {
		t.Fatalf("self-request spans: server=%d client=%d", serverSpans, clientSpans)
	}
}

func TestLoadRejectsUnboundedOrRecursiveTargets(t *testing.T) {
	app, _, _ := fixture(t)
	handler := app.handler()
	for _, path := range []string{"/load?requests=0", "/load?requests=1001", "/load?requests=x", "/load?target=load", "/load?target=http://example.com"} {
		if response := request(handler, path); response.Code != 400 {
			t.Errorf("%s: got %d", path, response.Code)
		}
	}
	app.loadSlot <- struct{}{}
	if response := request(handler, "/load"); response.Code != 429 {
		t.Fatalf("overlapping burst: got %d", response.Code)
	}
}

func TestOTLPHTTPExport(t *testing.T) {
	var mu sync.Mutex
	var received []*collectortrace.ExportTraceServiceRequest
	collector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/v1/traces" {
			t.Errorf("unexpected OTLP request: %s %s", r.Method, r.URL.Path)
		}
		data, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
			w.WriteHeader(500)
			return
		}
		payload := &collectortrace.ExportTraceServiceRequest{}
		if err := proto.Unmarshal(data, payload); err != nil {
			t.Error(err)
			w.WriteHeader(400)
			return
		}
		mu.Lock()
		received = append(received, payload)
		mu.Unlock()
		w.Header().Set("Content-Type", "application/x-protobuf")
		w.WriteHeader(http.StatusOK)
	}))
	defer collector.Close()
	t.Setenv("OTEL_EXPORTER_OTLP_ENDPOINT", collector.URL)
	t.Setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")
	t.Setenv("OTEL_EXPORTER_OTLP_HEADERS", "")
	t.Setenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "")
	t.Setenv("OTEL_SERVICE_NAME", "lab2-api-test")
	provider, err := newTracerProvider(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	defer provider.Shutdown(context.Background())
	logs := &logBuffer{}
	app := newApplication(provider, slog.New(slog.NewJSONHandler(logs, nil)))
	response := request(app.handler(), "/fail")
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := provider.ForceFlush(ctx); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	defer mu.Unlock()
	found := false
	for _, batch := range received {
		for _, resource := range batch.ResourceSpans {
			serviceName := ""
			for _, attr := range resource.Resource.Attributes {
				if attr.Key == "service.name" {
					serviceName = attr.Value.GetStringValue()
				}
			}
			for _, scope := range resource.ScopeSpans {
				for _, span := range scope.Spans {
					if span.Name == "GET /fail" && span.Status.Code == traceproto.Status_STATUS_CODE_ERROR &&
						hex.EncodeToString(span.TraceId) == response.Header().Get("X-Trace-ID") && serviceName == "lab2-api-test" {
						found = true
					}
				}
			}
		}
	}
	if !found {
		t.Fatal("OTLP receiver did not get the failed trace with the expected trace ID and service name")
	}
}
