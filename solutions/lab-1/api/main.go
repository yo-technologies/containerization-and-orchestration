package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

const mebibyte = 1024 * 1024

type application struct {
	ctx         context.Context
	memoryMu    sync.Mutex
	allocations [][]byte
	totalBytes  uint64
	burnOnce    sync.Once
	burnCycles  atomic.Uint64
	burnDone    chan struct{}
}

func newApplication(ctx context.Context) *application {
	return &application{ctx: ctx, burnDone: make(chan struct{})}
}

func (app *application) handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		_, _ = w.Write([]byte("ok\n"))
	})
	mux.HandleFunc("GET /eat", app.eat)
	mux.HandleFunc("GET /burn", app.burn)
	return mux
}

func (app *application) eat(w http.ResponseWriter, r *http.Request) {
	megabytes, err := strconv.Atoi(r.URL.Query().Get("mb"))
	maxInt := int(^uint(0) >> 1)
	if err != nil || megabytes <= 0 || megabytes > maxInt/mebibyte {
		http.Error(w, "mb must be a positive integer that fits the address space", http.StatusBadRequest)
		return
	}

	block := make([]byte, megabytes*mebibyte)
	// Touch each OS page: reserving virtual addresses alone does not prove
	// that the process consumes the requested amount of physical memory.
	for offset := 0; offset < len(block); offset += os.Getpagesize() {
		block[offset] = 1
	}
	block[len(block)-1] = 1

	app.memoryMu.Lock()
	// Retain the allocation after the HTTP handler returns so GC cannot free it.
	app.allocations = append(app.allocations, block)
	app.totalBytes += uint64(len(block))
	held := app.totalBytes
	app.memoryMu.Unlock()

	slog.Info("memory_held", "allocated_bytes", len(block), "held_bytes", held)
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]uint64{
		"allocated_bytes": uint64(len(block)),
		"held_bytes":      held,
	})
}

func (app *application) burn(w http.ResponseWriter, r *http.Request) {
	started := false
	app.burnOnce.Do(func() {
		started = true
		// A single busy goroutine can occupy roughly one core. Repeated /burn
		// requests do not multiply the load. It runs until the process stops.
		go func() {
			defer close(app.burnDone)
			for {
				select {
				case <-app.ctx.Done():
					return
				default:
				}
				for iteration := 0; iteration < 65536; iteration++ {
					// Observable side effects prevent compiler removal of the work.
					app.burnCycles.Add(1)
				}
			}
		}()
	})
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	_ = json.NewEncoder(w).Encode(map[string]bool{"running": true, "started": started})
}

func main() {
	listen := flag.String("listen", "127.0.0.1:18080", "HTTP listen address")
	flag.Parse()
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	app := newApplication(ctx)
	server := &http.Server{
		Addr:              *listen,
		Handler:           app.handler(),
		ReadHeaderTimeout: 5 * time.Second,
	}

	go func() {
		<-ctx.Done()
		shutdownCtx, stop := context.WithTimeout(context.Background(), 3*time.Second)
		defer stop()
		if err := server.Shutdown(shutdownCtx); err != nil {
			slog.Error("shutdown_failed", "error", err)
		}
	}()

	slog.Info("starting", "listen", *listen, "pid", os.Getpid(), "uid", os.Getuid())
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		slog.Error("serve_failed", "error", err)
		os.Exit(1)
	}
}
