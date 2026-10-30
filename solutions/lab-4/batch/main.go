package main

import (
	"context"
	"log/slog"
	"os"
	"os/signal"
	"runtime"
	"strconv"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

var sink atomic.Uint64

func envInt(name string, fallback, low, high int) int {
	raw := os.Getenv(name)
	if raw == "" {
		return fallback
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n < low || n > high {
		slog.Error("invalid setting", "name", name, "min", low, "max", high)
		os.Exit(2)
	}
	return n
}

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	memoryMiB := envInt("MEMORY_MIB", 32, 0, 4096)
	workers := envInt("CPU_WORKERS", 1, 0, 64)
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	// Touch every page so MEMORY_MIB causes resident memory use, not just reservation.
	memory := make([]byte, memoryMiB*1024*1024)
	touch := func() {
		for i := 0; i < len(memory); i += os.Getpagesize() {
			memory[i]++
		}
	}
	touch()
	slog.Info("batch started", "memory_mib", memoryMiB, "cpu_workers", workers)

	var wg sync.WaitGroup
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func(seed uint64) {
			defer wg.Done()
			x := seed + 1
			for {
				select {
				case <-ctx.Done():
					return
				default:
				}
				for j := 0; j < 65536; j++ {
					x = x*6364136223846793005 + 1
				}
				sink.Store(x)
			}
		}(uint64(i))
	}

	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			wg.Wait()
			runtime.KeepAlive(memory)
			slog.Info("batch stopped")
			return
		case <-ticker.C:
			touch()
		}
	}
}
