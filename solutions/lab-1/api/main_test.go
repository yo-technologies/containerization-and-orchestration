package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"runtime"
	"testing"
	"time"
)

func request(handler http.Handler, path string) *httptest.ResponseRecorder {
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, path, nil))
	return response
}

func TestHealth(t *testing.T) {
	response := request(newApplication(context.Background()).handler(), "/health")
	if response.Code != http.StatusOK || response.Body.String() != "ok\n" {
		t.Fatalf("health: status=%d body=%q", response.Code, response.Body.String())
	}
}

func TestEatRetainsTouchedPagesAcrossRequestsAndGC(t *testing.T) {
	app := newApplication(context.Background())
	handler := app.handler()
	for requestNumber := 1; requestNumber <= 2; requestNumber++ {
		response := request(handler, "/eat?mb=1")
		var result map[string]uint64
		if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		if response.Code != http.StatusOK || result["held_bytes"] != uint64(requestNumber*mebibyte) {
			t.Fatalf("eat: status=%d result=%v", response.Code, result)
		}
	}
	runtime.GC()
	if len(app.allocations) != 2 {
		t.Fatalf("expected 2 retained blocks; got %d", len(app.allocations))
	}
	for _, block := range app.allocations {
		for offset := 0; offset < len(block); offset += os.Getpagesize() {
			if block[offset] != 1 {
				t.Fatalf("page at offset %d was not touched", offset)
			}
		}
	}
}

func TestEatRejectsInvalidSizes(t *testing.T) {
	handler := newApplication(context.Background()).handler()
	for _, value := range []string{"", "0", "-1", "text", "1.5", "999999999999999999999999999"} {
		if response := request(handler, "/eat?mb="+value); response.Code != http.StatusBadRequest {
			t.Errorf("mb=%q: expected 400, got %d", value, response.Code)
		}
	}
}

func TestBurnStartsOnlyOneWorkerAndStopsWithContext(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	app := newApplication(ctx)
	handler := app.handler()
	for index := 0; index < 2; index++ {
		response := request(handler, "/burn")
		var result map[string]bool
		if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		if response.Code != http.StatusAccepted || result["started"] != (index == 0) {
			t.Fatalf("burn: status=%d result=%v", response.Code, result)
		}
	}
	deadline := time.Now().Add(2 * time.Second)
	for app.burnCycles.Load() == 0 && time.Now().Before(deadline) {
		runtime.Gosched()
	}
	if app.burnCycles.Load() == 0 {
		t.Fatal("CPU worker did not perform work")
	}
	if response := request(handler, "/health"); response.Code != http.StatusOK {
		t.Fatal("health failed while CPU worker was active")
	}
	cancel()
	select {
	case <-app.burnDone:
	case <-time.After(2 * time.Second):
		t.Fatal("CPU worker did not stop after cancellation")
	}
}
