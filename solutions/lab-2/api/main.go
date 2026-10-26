package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

func newTracerProvider(ctx context.Context) (*sdktrace.TracerProvider, error) {
	// The standard exporter reads OTEL_EXPORTER_OTLP_ENDPOINT and the
	// trace-specific endpoint setting. OTLP/HTTP uses port 4318 by default.
	exporter, err := otlptracehttp.New(ctx)
	if err != nil {
		return nil, fmt.Errorf("OTLP exporter: %w", err)
	}
	service := os.Getenv("OTEL_SERVICE_NAME")
	if service == "" {
		service = "api"
	}
	res, err := resource.New(ctx, resource.WithFromEnv(), resource.WithTelemetrySDK(),
		resource.WithAttributes(attribute.String("service.name", service)))
	if err != nil {
		_ = exporter.Shutdown(ctx)
		return nil, fmt.Errorf("OTel resource: %w", err)
	}
	return sdktrace.NewTracerProvider(
		sdktrace.WithResource(res),
		sdktrace.WithSampler(sdktrace.AlwaysSample()),
		sdktrace.WithBatcher(exporter),
	), nil
}

func run(ctx context.Context, logger *slog.Logger) (result error) {
	provider, err := newTracerProvider(ctx)
	if err != nil {
		return err
	}
	defer func() {
		flushCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		result = errors.Join(result, provider.Shutdown(flushCtx))
	}()
	address := os.Getenv("LISTEN_ADDR")
	if address == "" {
		address = ":8080"
	}
	listener, err := net.Listen("tcp", address)
	if err != nil {
		return err
	}
	defer listener.Close()
	host, port, err := net.SplitHostPort(listener.Addr().String())
	if err != nil {
		return err
	}
	if ip := net.ParseIP(host); ip != nil && ip.IsUnspecified() {
		if ip.To4() != nil {
			host = "127.0.0.1"
		} else {
			host = "::1"
		}
	}
	app := newApplication(provider, logger)
	app.selfURL = "http://" + net.JoinHostPort(host, port)
	server := &http.Server{Handler: app.handler(), ReadHeaderTimeout: 5 * time.Second,
		BaseContext: func(net.Listener) context.Context { return ctx }}
	done := make(chan error, 1)
	go func() { done <- server.Serve(listener) }()
	logger.Info("listening", "address", listener.Addr().String())
	select {
	case err := <-done:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdownCtx); err != nil {
			_ = server.Close()
			return err
		}
		return nil
	}
}

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	otel.SetErrorHandler(otel.ErrorHandlerFunc(func(err error) {
		logger.Error("telemetry_error", "error", err)
	}))
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if err := run(ctx, logger); err != nil {
		logger.Error("service_stopped", "error", err)
		os.Exit(1)
	}
}
