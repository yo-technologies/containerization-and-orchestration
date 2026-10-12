package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	if err := run(logger); err != nil {
		logger.Error("service stopped", "error", err)
		os.Exit(1)
	}
}

func run(logger *slog.Logger) error {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	n, err := strconv.Atoi(port)
	if err != nil || n < 1 || n > 65535 {
		return fmt.Errorf("PORT must be an integer between 1 and 65535")
	}
	serviceName := os.Getenv("OTEL_SERVICE_NAME")
	if serviceName == "" {
		serviceName = "api"
	}
	res, err := resource.New(context.Background(), resource.WithFromEnv(),
		resource.WithAttributes(attribute.String("service.name", serviceName)))
	if err != nil {
		return fmt.Errorf("initialize trace resource: %w", err)
	}
	opts := []sdktrace.TracerProviderOption{
		sdktrace.WithResource(res),
		sdktrace.WithSampler(sdktrace.AlwaysSample()),
	}
	exportEnabled := os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT") != "" || os.Getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") != ""
	if exportEnabled {
		exporter, err := otlptracehttp.New(context.Background())
		if err != nil {
			return fmt.Errorf("initialize OTLP/HTTP exporter: %w", err)
		}
		opts = append(opts, sdktrace.WithBatcher(exporter))
	}
	provider := sdktrace.NewTracerProvider(opts...)
	defer func() {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := provider.Shutdown(ctx); err != nil {
			logger.Error("flush traces", "error", err)
		}
	}()
	propagator := propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{})
	otel.SetTracerProvider(provider)
	otel.SetTextMapPropagator(propagator)
	otel.SetErrorHandler(otel.ErrorHandlerFunc(func(err error) {
		logger.Error("telemetry export failed", "error", err)
	}))
	app := newAPI(logger, provider, propagator, "http://127.0.0.1:"+port)
	defer app.client.CloseIdleConnections()
	server := &http.Server{
		Addr: ":" + port, Handler: app.handler(),
		ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 60 * time.Second,
		WriteTimeout: 30 * time.Second,
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	serverErrors := make(chan error, 1)
	go func() { serverErrors <- server.ListenAndServe() }()
	logger.Info("service starting", "port", n, "service", serviceName, "trace_export_enabled", exportEnabled)
	select {
	case err := <-serverErrors:
		return err
	case <-ctx.Done():
		logger.Info("service shutting down")
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := server.Shutdown(shutdownCtx); err != nil {
		_ = server.Close()
		return err
	}
	if err := <-serverErrors; !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}
