package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestHealthAndMetricsWithoutDatabase(t *testing.T) {
	for _, fail := range []bool{false, true} {
		a := newApplication(nil, "api", fail)
		handler := a.handler()
		health := httptest.NewRecorder()
		handler.ServeHTTP(health, httptest.NewRequest(http.MethodGet, "/health", nil))
		want := http.StatusOK
		if fail {
			want = http.StatusServiceUnavailable
		}
		if health.Code != want {
			t.Fatalf("HEALTH_FAIL=%v: got %d, want %d", fail, health.Code, want)
		}
		metrics := httptest.NewRecorder()
		handler.ServeHTTP(metrics, httptest.NewRequest(http.MethodGet, "/metrics", nil))
		if metrics.Code != http.StatusOK || !strings.Contains(metrics.Body.String(), "shop_http_requests_total") {
			t.Fatal("metrics must remain available even when health fails")
		}
	}
}

func TestOrderValidationAndDatabaseNotReady(t *testing.T) {
	handler := newApplication(nil, "api", false).handler()
	cases := []struct {
		body string
		code int
	}{
		{`{"item":"book","quantity":1}`, http.StatusServiceUnavailable},
		{`{"item":"","quantity":1}`, http.StatusBadRequest},
		{`{"item":"book","quantity":0}`, http.StatusBadRequest},
		{`{"item":"book","quantity":1,"unknown":true}`, http.StatusBadRequest},
		{`{"item":"book","quantity":1} {}`, http.StatusBadRequest},
		{strings.Repeat("x", 9000), http.StatusBadRequest},
	}
	for _, tc := range cases {
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/order", strings.NewReader(tc.body)))
		if response.Code != tc.code {
			t.Fatalf("body=%q: got %d, want %d", tc.body, response.Code, tc.code)
		}
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/orders", nil))
	if response.Code != http.StatusServiceUnavailable {
		t.Fatal("orders must return 503 until the database schema is ready")
	}
}

func TestWorkerDoesNotExposeOrderAPI(t *testing.T) {
	response := httptest.NewRecorder()
	newApplication(nil, "worker", false).handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/orders", nil))
	if response.Code != http.StatusNotFound {
		t.Fatal("worker must expose health/metrics but not the order API")
	}
}
